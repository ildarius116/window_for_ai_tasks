# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture

MWS GPT Platform — AI chat interface built on OpenWebUI (prebuilt image) with LiteLLM as a router, a custom Memory Service for persistent user memory, Langfuse for LLM tracing, and Prometheus+Grafana for metrics. All LLM traffic goes through a single upstream: **MWS GPT API** (`https://api.gpt.mws.ru/v1`, OpenAI-compatible).

```
User → Nginx (:80) → OpenWebUI (:8080 internal, :3000 host)
                        ↓
                      LiteLLM (:4000 internal) → MWS GPT API (https://api.gpt.mws.ru/v1)
                        ↓
              Memory Service (:8000 internal) ← OpenWebUI Filter (inlet/outlet)
              TTS Service (:8000 internal)    ← gTTS, OpenAI-compatible /v1/audio/speech
              Langfuse (:3000 internal)       ← tracing callbacks
              Prometheus (:9090 internal)     ← metrics scraping
              Grafana (:3000 internal, :3002 host) ← dashboards
```

> **Note:** Only nginx (:80), openwebui (:3000), and grafana (:3002) expose host ports. All other services are internal-only on `mws-network`.

**Request flow:** OpenWebUI treats LiteLLM as an OpenAI-compatible API (`OPENAI_API_BASE_URLS=http://litellm:4000/v1`). The `mws_memory` global filter function (inlet) searches Memory Service for relevant user memories and injects them into the system prompt before each request. After responses (outlet), it periodically extracts new facts via LLM. LiteLLM forwards every request to MWS GPT API and sends traces to Langfuse. **Embeddings (RAG) and STT (voice input) also route through LiteLLM**, not through local models.

**Model aliases** (defined in `litellm/config.yaml` — 26 aliases, all pointing to `openai/<model>` with `api_base: https://api.gpt.mws.ru/v1` and `api_key: os.environ/MWS_GPT_API_KEY`):

- **Chat / instruct:** `mws/gpt-alpha` (default), `mws/qwen2.5-72b`, `mws/qwen3-235b`, `mws/qwen3-32b`, `mws/qwen3-coder`, `mws/llama-3.1-8b`, `mws/llama-3.3-70b`, `mws/gpt-oss-120b`, `mws/gpt-oss-20b`, `mws/glm-4.6`, `mws/kimi-k2`, `mws/deepseek-r1-32b`, `mws/qwq-32b`, `mws/gemma-3-27b`, `mws/t-pro`
- **Vision:** `mws/qwen3-vl`, `mws/qwen2.5-vl`, `mws/qwen2.5-vl-72b`, `mws/cotype-pro-vl`
- **Embeddings:** `mws/bge-m3`, `mws/bge-gemma2`, `mws/qwen3-embedding`
- **STT (whisper):** `mws/whisper-medium`, `mws/whisper-turbo`
- **Image generation:** `mws/qwen-image`, `mws/qwen-image-lightning`

**Router settings** (`litellm/config.yaml`):
- `routing_strategy: simple-shuffle`, `num_retries: 2`, `timeout: 120`
- Fallbacks: `mws/gpt-alpha → [mws/qwen3-235b, mws/llama-3.3-70b]`, `mws/qwen3-coder → [mws/qwen3-235b, mws/gpt-oss-120b]`, `mws/qwen3-235b → [mws/gpt-alpha, mws/llama-3.3-70b]`, `mws/gpt-oss-120b → [mws/qwen3-235b, mws/llama-3.3-70b]`
- Redis response cache enabled (`host: redis, port: 6379`)
- `success_callback: ["langfuse"]`, `failure_callback: ["langfuse"]`
- `drop_params: true` (strips unsupported params rather than failing)
- `max_internal_user_budget: 10`, `internal_user_budget_duration: "1mo"`

**Databases** (single `pgvector/pgvector:pg16` instance, auto-created via `scripts/init-databases.sql`):
- `openwebui` — users, chats, settings
- `litellm` — spend tracking, API keys
- `langfuse` — LLM tracing data
- `memory` — pgvector-enabled, user memories with embeddings

**Voice:**
- **STT** is routed via LiteLLM to `mws/whisper-turbo` (no local faster-whisper). OpenWebUI env: `AUDIO_STT_ENGINE=openai`, `AUDIO_STT_OPENAI_API_BASE_URL=http://litellm:4000/v1`, `AUDIO_STT_MODEL=mws/whisper-turbo`.
- **TTS** uses the local `tts-service` companion (gTTS) exposed as OpenAI-compatible API. OpenWebUI env: `AUDIO_TTS_ENGINE=openai`, `AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-service:8000/v1`, `AUDIO_TTS_MODEL=tts-1` (name is a placeholder — gTTS ignores it).

**RAG:** OpenWebUI embeddings are routed via LiteLLM to `mws/bge-m3`. OpenWebUI env: `RAG_EMBEDDING_ENGINE=openai`, `RAG_EMBEDDING_MODEL=mws/bge-m3`, `RAG_OPENAI_API_BASE_URL=http://litellm:4000/v1`. No HuggingFace download happens at startup. Files are uploaded via `/api/v1/files/` and indexed via `/api/v1/retrieval/process/file`. Knowledge bases via `/api/v1/knowledge/`.

**Services (10 total):** postgres (pgvector), redis, litellm, openwebui, memory-service, tts-service, langfuse, prometheus, grafana, nginx.

## Commands

```bash
# First-time setup
cp .env.example .env            # fill in MWS_GPT_API_KEY + Langfuse keys
make gen-secrets                # copy output into .env
make build                      # build LiteLLM + memory-service + tts-service images
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
- **LiteLLM model aliases** (`mws/*`) are the canonical model names. Never hardcode upstream MWS GPT model IDs (like `mws-gpt-alpha`, `qwen3-coder-480b-a35b`) outside `litellm/config.yaml`. The one exception is `DEFAULT_MODELS: "mws/gpt-alpha"` in `docker-compose.yml`, which sets OpenWebUI's default picker — it uses an alias, not a raw model ID.
- **API keys** — only `MWS_GPT_API_KEY` is required for model traffic. It's the single shared key used by every LiteLLM `model_list` entry. All prior multi-provider keys (Anthropic, OpenAI direct, OpenRouter, DashScope) have been removed.
- **LiteLLM container** also reads `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` for tracing, and `LITELLM_MASTER_KEY` + `LITELLM_DATABASE_URL` for the proxy itself.
- **LiteLLM healthcheck** uses python urllib (no curl in container).
- **Langfuse integration**: LiteLLM sends success/failure callbacks to Langfuse automatically. Keys are created in Langfuse UI and set in `.env`.
- **Memory Service** (`memory-service/`) is a FastAPI microservice with pgvector. It calls LiteLLM for embeddings and for LLM-based fact extraction. ⚠️ **Known bug:** the default model names in `memory-service/app/config.py` and in `docker-compose.yml` (service `memory-service`, env `EXTRACTION_MODEL: mws/nemotron`) point to `mws/nemotron` / `mws/nemotron-nano`, which no longer exist in `litellm/config.yaml`. When touching Memory Service, update these to valid aliases (e.g., `EXTRACTION_MODEL=mws/gpt-alpha`, embedding model to `mws/bge-m3`). Embedding code in `embedding.py` also has a hardcoded fallback to `text-embedding-3-small` which will always fail and drop back to hash-based pseudo-embeddings.
- **OpenWebUI Functions** (filters) are managed via `/api/v1/functions/`. **Tools** use a separate API: `/api/v1/tools/`. The `mws_memory` filter is global and active. Tools `mws_memory_tool` and `mws_usage_stats` are available in chat.
- **TTS Service** (`tts-service/`) is a gTTS-based companion. Exposes `POST /v1/audio/speech` for OpenAI-SDK compatibility. Voice/model names in the request body are accepted but ignored.
- **Proxy caveat**: The dev machine has `HTTP_PROXY`/`HTTPS_PROXY` set. Use `HTTP_PROXY= HTTPS_PROXY=` prefix or `--noproxy localhost` with curl for local requests. Inside containers, proxy vars are not set, so outbound calls to `api.gpt.mws.ru` go direct.
- All services communicate over Docker network `mws-network` using container names.
- **Production deployment** uses `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`. The prod override adds resource limits, log rotation, and `restart: always`.
- **Host port exposure** is minimized for security: only nginx (:80), openwebui (:3000), and grafana (:3002) are exposed. All other services communicate internally via `mws-network`.
- **Nginx security**: rate limiting (10 req/s general, 5 req/s API), security headers, attack path blocking are configured. HTTPS config is commented out, ready for certificates.

## Key Files

- `litellm/config.yaml` — model definitions (26 MWS GPT aliases), routing, fallbacks, Redis cache, Langfuse callback settings
- `docker-compose.yml` — full 10-service stack definition and env var mappings
- `docker-compose.prod.yml` — production overrides (resource limits, logging, restart policy)
- `memory-service/app/` — FastAPI app: `models.py` (SQLAlchemy+pgvector), `routers/memories.py` (CRUD+search+extract), `embedding.py`, `extraction.py`, `config.py` (⚠️ broken default model names)
- `tts-service/main.py` — gTTS-based OpenAI-compatible TTS endpoint
- `pipelines/memory_function.py` — OpenWebUI filter function source (deployed via API as `mws_memory`)
- `pipelines/memory_tool.py` — OpenWebUI Tool: view/search/delete user memories from chat
- `pipelines/usage_stats_tool.py` — OpenWebUI Tool: model usage and spend statistics
- `openwebui/static/custom.css` — MWS custom theme (volume-mounted into container)
- `monitoring/prometheus.yml` — Prometheus scrape config
- `monitoring/grafana/` — provisioning + dashboards JSON
- `nginx/nginx.conf` — reverse proxy with rate limiting, security headers, attack path blocking, HTTPS-ready
- `scripts/init-databases.sql` — PostgreSQL multi-database initialization
- `scripts/check-secrets.sh` — `.env` validation and secret leak detection
- `scripts/backup.sh` / `scripts/restore.sh` — PostgreSQL backup and restore
- `.env` — all secrets and API keys (not committed)
- `.env.example` — template, includes `MWS_GPT_API_KEY` as the only LLM provider key
- `MWS_GPT_API_docs.pdf` — upstream provider's API reference (endpoints: `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`)
- `models.json` — full model list returned by `GET https://api.gpt.mws.ru/v1/models` (source of truth for what aliases can be added to `litellm/config.yaml`)
- `README_proj.md` — project-specific README (separate from the repo root `README.md`)

## Web UIs

| Service | URL | Credentials | Notes |
|---------|-----|-------------|-------|
| OpenWebUI | http://localhost (nginx) or :3000 | First registered user = admin | |
| Grafana | http://localhost:3002 | admin / admin (or `GF_SECURITY_ADMIN_PASSWORD` / `GRAFANA_ADMIN_PASSWORD`) | |
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

Development is organized through 15 specialized subagents defined in `.claude/agents.json` and described in `project_assignment.md`. The team lead (Opus) coordinates and delegates ALL implementation work to worker subagents (Sonnet). The main context is strictly for coordination, planning, and user communication — never for direct implementation.

Key agents: ArchitectAgent, DevOpsAgent, SecurityAgent, BackendCoderAgent, AIRouterAgent, MemoryAgent, VoiceAgent, FileAgent, FrontendAgent/OpenWebUIAgent, ReviewerAgent, TesterAgent, DocsAgent, SkillCreatorAgent. Each has mapped MCP skills in `.claude/skills/`.

## Project Status

All 8 phases of the initial build are **DONE** (completed 2026-03-28 — 2026-03-29). Reports in `tasks_done/phase-{1..8}-done.md`. The plan is in `PLAN.md`, detailed assignment in `project_assignment.md`.

**Post-Phase-8 migration (2026-04-10):** The stack was reworked to use a single provider — MWS GPT API — replacing the prior multi-provider setup (Anthropic, OpenAI direct, OpenRouter, DashScope). The smart `mws/auto` router was removed. RAG and STT were also migrated from local models (sentence-transformers, faster-whisper) to MWS GPT via LiteLLM to avoid HuggingFace downloads at first boot. Memory Service still has outdated default model names — see the warning above.
