# MWS GPT Platform

Self-hosted AI chat platform with multi-provider routing, long-term memory, voice, and monitoring.

Built on [OpenWebUI](https://github.com/open-webui/open-webui) (prebuilt image) with [LiteLLM](https://github.com/BerriAI/litellm) as the AI gateway, a custom Memory Service for persistent user memory, and a full observability stack.

## Features

- **Multi-model chat** -- Anthropic Claude, OpenAI GPT-4o/o3, Qwen, NVIDIA Nemotron, and more via a single interface
- **Smart routing** -- `mws/auto` analyzes query complexity and picks the right model (Sonnet for simple, Opus for complex)
- **Fallback chains** -- if a provider is down or out of budget, requests fall back to alternative models automatically
- **Long-term memory** -- a filter extracts facts from conversations and injects relevant memories into future prompts
- **Voice** -- speech-to-text (local Whisper) and text-to-speech (gTTS, OpenAI-compatible API)
- **RAG** -- upload PDFs, DOCX, CSV and ask questions; embeddings via local sentence-transformers
- **Usage tracking** -- per-user budgets, spend tracking, model usage stats accessible from chat
- **LLM tracing** -- every request traced in Langfuse for debugging and cost analysis
- **Monitoring** -- Prometheus metrics with pre-built Grafana dashboards
- **Security** -- nginx rate limiting, security headers, attack path blocking, no-new-privileges on all containers

## Architecture

```
User --> Nginx (:80) --> OpenWebUI (:8080)
                              |
                         LiteLLM (:4000) --> Anthropic API
                              |              OpenAI API
                              |              OpenRouter (free models)
                              |              DashScope / Qwen API
                              |
                    Memory Service (:8001)  <-- OpenWebUI filter (inlet/outlet)
                    TTS Service (:8002)     <-- gTTS
                    Langfuse (:3001)        <-- LLM tracing
                    Prometheus (:9090)      <-- metrics
                    Grafana (:3002)         <-- dashboards
```

OpenWebUI sends all LLM requests to LiteLLM (`OPENAI_API_BASE_URLS=http://litellm:4000/v1`). The `mws_memory` global filter searches the Memory Service for relevant user memories and injects them into the system prompt before each request. LiteLLM translates requests to each provider's native format and sends traces to Langfuse.

## Services

| Service | Image / Build | Host Port | Description |
|---------|---------------|-----------|-------------|
| **postgres** | pgvector/pgvector:pg16 | -- | PostgreSQL with pgvector; 4 databases (openwebui, litellm, langfuse, memory) |
| **redis** | redis:7-alpine | -- | Caching for LiteLLM response cache |
| **litellm** | build: ./litellm | -- | AI gateway, model routing, fallbacks, spend tracking |
| **openwebui** | ghcr.io/open-webui/open-webui:main | 3000 | Chat UI, RAG, file uploads, admin settings |
| **memory-service** | build: ./memory-service | -- | FastAPI + pgvector; stores and searches user memories |
| **tts-service** | build: ./tts-service | -- | gTTS-based text-to-speech, OpenAI-compatible API |
| **langfuse** | langfuse/langfuse:2 | -- | LLM tracing and analytics |
| **prometheus** | prom/prometheus:latest | -- | Metrics collection (30d retention) |
| **grafana** | grafana/grafana:latest | 3002 | Dashboards and alerting |
| **nginx** | nginx:alpine | 80 | Reverse proxy with rate limiting and security headers |

Internal services (marked `--`) are accessible only via the Docker network, not exposed to the host.

## Models

| Alias | Provider | Underlying Model | Notes |
|-------|----------|-------------------|-------|
| `mws/auto` | Smart Router | complexity_router | Auto-selects Sonnet or Opus based on query complexity |
| `mws/sonnet` | Anthropic | claude-sonnet-4-6 | Direct API, requires balance |
| `mws/opus` | Anthropic | claude-opus-4-6 | Direct API, requires balance |
| `mws/gpt-4o` | OpenAI | gpt-4o | Direct API |
| `mws/gpt-4o-mini` | OpenAI | gpt-4o-mini | Direct API, cost-efficient |
| `mws/o3-mini` | OpenAI | o3-mini | Direct API, reasoning model |
| `mws/nemotron` | OpenRouter | nvidia/nemotron-3-super-120b | Free tier |
| `mws/nemotron-nano` | OpenRouter | nvidia/nemotron-nano-9b-v2 | Free tier |
| `mws/qwen-coder` | OpenRouter | qwen/qwen3-coder | Free tier |
| `mws/qwen-or` | OpenRouter | qwen/qwen3-next-80b | Free tier |
| `mws/qwen` | DashScope | qwen-plus | Requires QWEN_API_KEY |

Fallback chains: Opus -> Sonnet -> GPT-4o -> Nemotron; Sonnet -> Opus -> GPT-4o -> Nemotron; Auto -> GPT-4o-mini -> Nemotron.

## Quick Start

### Prerequisites

- Docker and Docker Compose v2
- `make` (optional but recommended)
- API keys: at minimum, an OpenRouter key (free) for `mws/nemotron` models

### 1. Clone and configure

```bash
git clone <repository-url>
cd mws-gpt
cp .env.example .env
```

Edit `.env` and fill in:
- `ANTHROPIC_API_KEY` -- for Claude models (optional if using free models only)
- `OPENAI_API_KEY` -- OpenAI API key for GPT-4o, GPT-4o-mini, o3-mini (optional)
- `OPENROUTER_API_KEY` -- OpenRouter API key for free models (Nemotron, Qwen-coder, etc.)
- `QWEN_API_KEY` -- for Qwen via DashScope (optional)

Generate secrets for the remaining keys:

```bash
make gen-secrets
# Copy the output values into .env
```

### 2. Build and start

```bash
make build
make setup
```

### 3. Create admin account

Open http://localhost:3000 and register. The first user becomes admin.

## Commands

| Command | Description |
|---------|-------------|
| `make up` | Start all services |
| `make down` | Stop all services |
| `make ps` | Show service status |
| `make build` | Build custom images (litellm, memory-service, tts-service) |
| `make setup` | Start services and print setup instructions |
| `make logs` | Tail logs for all services |
| `make logs-openwebui` | Tail OpenWebUI logs |
| `make logs-litellm` | Tail LiteLLM logs |
| `make gen-secrets` | Generate random secrets for .env |
| `make reset` | Destroy volumes and rebuild (destructive) |
| `make prod` | Start with production overrides |
| `make backup` | Backup all 4 PostgreSQL databases |
| `make restore DB=<db> FILE=<path>` | Restore a specific database from backup |

## Web UIs

| Service | URL | Credentials |
|---------|-----|-------------|
| OpenWebUI | http://localhost (nginx) or http://localhost:3000 (direct) | First registered user = admin |
| Langfuse | http://localhost:3001 | Created on first visit |
| Grafana | http://localhost:3002 | admin / admin (or `GRAFANA_ADMIN_PASSWORD`) |
| Prometheus | http://localhost:9090 | No auth |
| Memory Service API | http://localhost:8001/docs | No auth |

## Production

Use `docker-compose.prod.yml` for production deployments:

```bash
make prod
# or: docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Production overrides include:
- **Resource limits** -- memory and CPU caps for all 10 services (~4.5 GB total RAM)
- **Log rotation** -- json-file driver, 10 MB per file, 3 files per service
- **Restart policy** -- `always` instead of `unless-stopped`

### Backups

```bash
make backup                              # dumps all 4 databases to backups/
make restore DB=memory FILE=backups/memory_2026-03-29_120000.sql.gz
```

Backups older than 7 days are automatically cleaned up.

## Security

- **Nginx hardening** -- rate limiting (10 req/s general, 5 req/s API), security headers (X-Frame-Options, CSP, X-Content-Type-Options), blocked attack paths (.env, .git, wp-admin, phpmyadmin, etc.)
- **Container isolation** -- `no-new-privileges` on all services; read-only filesystems on nginx and prometheus; internal services not exposed to host
- **Secrets validation** -- run `bash scripts/check-secrets.sh` to verify .env completeness, detect weak passwords, and scan for leaked API keys in tracked files
- **HTTPS ready** -- nginx config includes commented SSL block for TLS 1.2/1.3 with modern ciphersuites

## Key Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Full 10-service stack |
| `docker-compose.prod.yml` | Production overrides (limits, logging) |
| `litellm/config.yaml` | Model definitions, routing, fallbacks, cache |
| `memory-service/app/` | Memory Service source (FastAPI + pgvector) |
| `tts-service/main.py` | TTS endpoint (gTTS) |
| `pipelines/memory_function.py` | OpenWebUI filter for memory injection |
| `pipelines/memory_tool.py` | Chat tool for viewing/managing memories |
| `pipelines/usage_stats_tool.py` | Chat tool for usage statistics |
| `pipelines/auto_router_function.py` | **MWS GPT Auto 🎯** — auto-router Pipe function (phase 9) |
| `PLAN_chat_agents.md` | Master design doc for the auto-router |
| `model_capabilities.md` | Curated model-to-task map used by the router |
| `nginx/nginx.conf` | Reverse proxy with security config |
| `monitoring/` | Prometheus config and Grafana dashboards |
| `scripts/` | Database init, backup, restore, secrets check |
| `.env.example` | Template for environment variables |
| `CLAUDE.md` | AI agent instructions |

## How to use MWS GPT Auto 🎯

`MWS GPT Auto 🎯` is a virtual model in the OpenWebUI dropdown that auto-selects the right MWS model for every request.

### Zero-config startup

```bash
cp .env.example .env          # fill MWS_GPT_API_KEY and secrets
docker compose up -d          # OR: make up
# → open http://localhost:3000 → sign up (first account becomes admin)
```

That's it. The stack runs an init sidecar (`bootstrap` service) that waits for the first signup and then seeds both `auto_router_function.py` and `memory_function.py` directly into OpenWebUI's database with `is_active=true` and `is_global=true`. After signup, reload the page once and `MWS GPT Auto 🎯` appears at the top of the model dropdown; the MWS Memory filter is already attached to every chat.

No manual function upload, no API token, no additional commands.

If you later edit a function source file and want to re-deploy it without restarting the stack, run `make deploy-functions` (requires `OWUI_ADMIN_TOKEN` for the HTTP API path).

### What it does automatically

| You send… | It dispatches to… |
|---|---|
| Plain text ("Расскажи про X") | `sa_ru_chat` (`mws/t-pro`) or `sa_general` (`mws/gpt-alpha`) |
| Code question ("write fibonacci in rust") | `sa_code` (`mws/qwen3-coder`) |
| Math/logic puzzle | `sa_reasoner` (`mws/deepseek-r1-32b`) |
| Attached image | `sa_vision` (`mws/cotype-pro-vl` / `mws/qwen2.5-vl-72b`) |
| Attached audio (`.mp3`, `.wav`) | `sa_stt` (`mws/whisper-turbo`) → re-planned by transcript |
| Attached PDF/DOCX | `sa_doc_qa` (`mws/glm-4.6`, via built-in RAG) |
| "Нарисуй …" / "generate image …" | `sa_image_gen` (`mws/qwen-image`) |
| "Найди в интернете …" | `sa_web_search` (DuckDuckGo + `mws/kimi-k2`) |
| A message with `https://…` | `sa_web_fetch` (`mws/llama-3.1-8b`) |

Every response begins with a collapsible **🎯 Routing decision** block showing the detected language, chosen subagents and models. Each subagent runs in parallel, returns a compact summary (≤500 tokens), and the final `mws/t-pro`/`mws/gpt-alpha` aggregator streams the answer in markdown — without ever seeing the sub-responses' raw chain-of-thought.

### Manual model override

The dropdown also lists every raw `mws/*` alias from LiteLLM (26 models). Pick one manually — e.g. `mws/deepseek-r1-32b` — and the auto-router is **completely bypassed**: the request goes straight to LiteLLM. Use this when you want deterministic model selection or fine control.

### Features covered (10 mandatory + 2 stubs)

Text chat, voice chat (STT), image generation, audio-file + ASR, image analysis (VLM), file Q&A, web search, URL parsing, long-term memory (via existing `memory_function.py`), auto-select, manual-select, markdown + code — all working out of the box. Deep Research and slide-deck generation are v1 stubs and return a "will be added in v2" notice while still being classifiable.

See `PLAN_chat_agents.md` for the full design and `tasks_done/phase-9-done.md` for the verification report.

## License

TBD
