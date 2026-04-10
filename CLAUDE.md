# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

MWS GPT Platform — AI chat interface built on OpenWebUI (prebuilt image) with LiteLLM as a multi-provider AI router, a custom Memory Service for persistent user memory, Langfuse for LLM tracing, and Prometheus+Grafana for metrics.

```
User → Nginx (:80) → OpenWebUI (:8080 internal, :3000 host)
                        ↓
                      LiteLLM (:4000 internal) → Anthropic API
                                               → OpenAI API
                                               → OpenRouter (free models)
                                               → DashScope / Qwen API
                        ↓
              Memory Service (:8000 internal) ← OpenWebUI Filter (inlet/outlet)
              TTS Service (:8000 internal)    ← gTTS, OpenAI-compatible /v1/audio/speech
              Langfuse (:3000 internal)       ← tracing callbacks
              Prometheus (:9090 internal)     ← metrics scraping
              Grafana (:3000 internal, :3002 host) ← dashboards
```

> **Note:** After Phase 8 security hardening, only nginx (:80), openwebui (:3000), and grafana (:3002) expose host ports. All other services are internal-only on `mws-network`.

**Request flow:** OpenWebUI treats LiteLLM as an OpenAI-compatible API (`OPENAI_API_BASE_URLS=http://litellm:4000/v1`). The `mws_memory` global filter function (inlet) searches Memory Service for relevant user memories and injects them into the system prompt before each request. After responses (outlet), it periodically extracts new facts via LLM. LiteLLM translates requests to each provider's native format and sends traces to Langfuse.

**Model providers and aliases** (defined in `litellm/config.yaml`):
- `mws/sonnet`, `mws/opus` — Anthropic direct (require `ANTHROPIC_API_KEY` with balance)
- `mws/gpt-4o`, `mws/gpt-4o-mini`, `mws/o3-mini` — OpenAI direct (use `OPENAI_API_KEY`)
- `mws/nemotron`, `mws/nemotron-nano`, `mws/qwen-coder`, `mws/qwen-or` — OpenRouter free tier (use `OPENROUTER_API_KEY`)
- `mws/qwen` — Qwen Plus via DashScope (uses `QWEN_API_KEY`)
- `mws/auto` — Smart router (complexity_router): auto-selects Sonnet for simple tasks, Opus for complex. Falls back to gpt-4o-mini then free models.

**Databases** (single pgvector/pgvector:pg16 instance, auto-created via `scripts/init-databases.sql`):
- `openwebui` — users, chats, settings
- `litellm` — spend tracking, API keys
- `langfuse` — LLM tracing data
- `memory` — pgvector-enabled, user memories with embeddings

**Voice:** STT uses built-in faster-whisper (`base` model, local). TTS uses `tts-service` companion (gTTS) exposed as OpenAI-compatible API.

**Services (10 total):** postgres (pgvector), redis, litellm, openwebui, memory-service, tts-service, langfuse, prometheus, grafana, nginx.

## Commands

```bash
# First-time setup
cp .env.example .env            # fill in API keys + Langfuse keys
make gen-secrets                 # copy output into .env
make build                      # build LiteLLM + memory-service images
make setup                      # starts stack + prints next steps

# Daily
make up / make down / make ps
make logs                       # all services
make logs-openwebui             # just OpenWebUI
make logs-litellm               # just LiteLLM
make reset                      # nuke volumes and rebuild (destructive)

# Adding/changing models: edit litellm/config.yaml, then:
docker compose build litellm && docker compose up -d

# When .env changes (new API keys):
docker compose up -d --force-recreate litellm

# Memory Service development:
docker compose build memory-service && docker compose up -d memory-service

# TTS Service development:
docker compose build tts-service && docker compose up -d tts-service

# Production
make prod                       # start with production resource limits
make backup                     # dump all 4 PostgreSQL databases
make restore DB=openwebui FILE=backups/openwebui_2026-03-29.sql.gz  # restore from backup

# Security check
bash scripts/check-secrets.sh   # validate .env, check for leaked secrets
```

## Development Conventions

- **OpenWebUI runs as a prebuilt image** (`ghcr.io/open-webui/open-webui:main`) — no local source to edit. Customize via env vars, OpenWebUI Functions (uploaded via API), or Admin Settings in the UI.
- **LiteLLM model aliases** (`mws/*`) are the canonical model names. Never hardcode provider model IDs (like `claude-sonnet-4-6`) outside `litellm/config.yaml`.
- **API keys** are passed directly to LiteLLM: `OPENAI_API_KEY` for OpenAI models, `OPENROUTER_API_KEY` for OpenRouter free models, `ANTHROPIC_API_KEY` for Anthropic, `QWEN_API_KEY` for DashScope.
- **LiteLLM healthcheck** uses python urllib (no curl in container).
- **Langfuse integration**: LiteLLM sends success/failure callbacks to Langfuse automatically. Keys are created in Langfuse UI and set in `.env`.
- **Memory Service** (`memory-service/`) is a FastAPI microservice with pgvector. Uses hash-based pseudo-embeddings as MVP fallback (real embeddings when an embedding model is available).
- **OpenWebUI Functions** (filters) are managed via `/api/v1/functions/`. **Tools** use a separate API: `/api/v1/tools/`. The `mws_memory` filter is global and active. Tools `mws_memory_tool` and `mws_usage_stats` are available in chat.
- **TTS Service** (`tts-service/`) is a gTTS-based companion. OpenWebUI connects to it via `AUDIO_TTS_ENGINE=openai` + `AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-service:8000/v1`.
- **STT** uses OpenWebUI's built-in faster-whisper. `AUDIO_STT_ENGINE=""` (empty string) activates local Whisper — NOT `"whisper-local"` despite what the UI shows.
- **RAG**: OpenWebUI uses built-in `sentence-transformers/all-MiniLM-L6-v2` for embeddings (runs locally, no external API needed). Files are uploaded via `/api/v1/files/` and indexed via `/api/v1/retrieval/process/file`. Knowledge bases via `/api/v1/knowledge/`.
- **Proxy caveat**: The dev machine has `HTTP_PROXY`/`HTTPS_PROXY` set. Use `HTTP_PROXY= HTTPS_PROXY=` prefix or `--noproxy localhost` with curl for local requests.
- All services communicate over Docker network `mws-network` using container names.
- **Production deployment** uses `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`. The prod override adds resource limits, log rotation, and `restart: always`.
- **Host port exposure** is minimized for security: only nginx (:80), openwebui (:3000), and grafana (:3002) are exposed. All other services communicate internally via `mws-network`.
- **Nginx security**: rate limiting (10 req/s general, 5 req/s API), security headers, attack path blocking are configured. HTTPS config is commented out, ready for certificates.

## Key Files

- `litellm/config.yaml` — model definitions, routing, fallbacks, cache and Langfuse callback settings
- `docker-compose.yml` — full 10-service stack definition and env var mappings
- `memory-service/app/` — FastAPI app: models.py (SQLAlchemy+pgvector), routers/memories.py (CRUD+search+extract), embedding.py, extraction.py
- `tts-service/main.py` — gTTS-based OpenAI-compatible TTS endpoint
- `pipelines/memory_function.py` — OpenWebUI filter function source (deployed via API as `mws_memory`)
- `pipelines/memory_tool.py` — OpenWebUI Tool: view/search/delete user memories from chat
- `pipelines/usage_stats_tool.py` — OpenWebUI Tool: model usage and spend statistics
- `openwebui/static/custom.css` — MWS custom theme (volume-mounted into container)
- `monitoring/prometheus.yml` — Prometheus scrape config
- `monitoring/grafana/` — provisioning + dashboards JSON
- `nginx/nginx.conf` — reverse proxy with rate limiting, security headers, attack path blocking, HTTPS-ready
- `scripts/init-databases.sql` — PostgreSQL multi-database initialization
- `.env` — all secrets and API keys (not committed)
- `docker-compose.prod.yml` — production overrides (resource limits, logging, restart policy)
- `scripts/check-secrets.sh` — .env validation and secret leak detection
- `scripts/backup.sh` / `scripts/restore.sh` — PostgreSQL backup and restore

## Web UIs

| Service | URL | Credentials | Notes |
|---------|-----|-------------|-------|
| OpenWebUI | http://localhost (nginx) or :3000 | First registered user = admin | |
| Grafana | http://localhost:3002 | admin / admin (or `GRAFANA_ADMIN_PASSWORD`) | |
| Langfuse | Internal only (was :3001) | Created on first visit | Expose via `docker compose port` if needed |
| Prometheus | Internal only (was :9090) | No auth | Accessed by Grafana internally |
| Memory Service | Internal only (was :8001) | No auth | Access via `docker compose exec` |

## Memory Service API

```
POST   /memories              — save a memory (user_id, content, source_chat_id)
GET    /memories/{user_id}    — list user's memories
POST   /memories/search       — semantic search (user_id, query, limit)
POST   /memories/extract      — LLM-extract facts from conversation messages
DELETE /memories/{id}          — delete specific memory
DELETE /memories/user/{id}     — delete all user memories
GET    /health                — healthcheck
```

## Agent System

Development is organized through 14+ specialized subagents defined in `.claude/agents.json` and described in `project_assignment.md`. The team lead (Opus) coordinates and delegates ALL implementation work to worker subagents (Sonnet). The main context is strictly for coordination, planning, and user communication — never for direct implementation.

Key agents: ArchitectAgent, DevOpsAgent, SecurityAgent, BackendCoderAgent, AIRouterAgent, MemoryAgent, VoiceAgent, FileAgent, FrontendAgent/OpenWebUIAgent, ReviewerAgent, TesterAgent, DocsAgent, SkillCreatorAgent. Each has mapped MCP skills in `.claude/skills/`.

## Project Status

All 8 phases are **DONE** (completed 2026-03-28 — 2026-03-29). Reports in `tasks_done/phase-{1..8}-done.md`. The plan is in `PLAN.md`, detailed assignment in `project_assignment.md`.
