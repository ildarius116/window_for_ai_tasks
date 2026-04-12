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

**Postgres persistence**: the data directory is a **host bind-mount** `./data/postgres:/var/lib/postgresql/data` (see `docker-compose.yml`), not a named volume. Rationale: survives `docker compose down -v` / `make reset` / container rebuilds without any migration step. Caveat: to truly wipe the DB, manually `rm -rf ./data/postgres` — `-v` alone no longer does it. `./data/` is gitignored.

**Voice:**
- **STT** is routed via LiteLLM to `mws/whisper-turbo` (no local faster-whisper). OpenWebUI env: `AUDIO_STT_ENGINE=openai`, `AUDIO_STT_OPENAI_API_BASE_URL=http://litellm:4000/v1`, `AUDIO_STT_MODEL=mws/whisper-turbo`.
- **TTS** uses the local `tts-service` companion (gTTS) exposed as OpenAI-compatible API. OpenWebUI env: `AUDIO_TTS_ENGINE=openai`, `AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-service:8000/v1`, `AUDIO_TTS_MODEL=tts-1` (name is a placeholder — gTTS ignores it).

**RAG:** OpenWebUI embeddings are routed via LiteLLM to `mws/bge-m3`. OpenWebUI env: `RAG_EMBEDDING_ENGINE=openai`, `RAG_EMBEDDING_MODEL=mws/bge-m3`, `RAG_OPENAI_API_BASE_URL=http://litellm:4000/v1`. No HuggingFace download happens at startup. Files are uploaded via `/api/v1/files/` and indexed via `/api/v1/retrieval/process/file`. Knowledge bases via `/api/v1/knowledge/`.

**Services (11 total):** postgres (pgvector), redis, litellm, openwebui, memory-service, tts-service, langfuse, prometheus, grafana, nginx, **bootstrap** (one-shot init).

**Zero-config startup:** the stack is designed to come up with a single `docker compose up -d` (or `make up`) — no follow-up commands. A small `bootstrap` sidecar (`python:3.11-slim` + `scripts/bootstrap.py`) waits for postgres, waits for OpenWebUI's migrations to create the `function`/`user` tables, then polls for the first user signup. The moment the operator creates an admin account via the OpenWebUI web UI (first-signup-becomes-admin, default flow), the sidecar UPSERTs `pipelines/auto_router_function.py` and `pipelines/memory_function.py` directly into postgres with `is_active=true, is_global=true` — so `MWS GPT Auto 🎯` appears in the model dropdown and the `mws_memory` filter attaches to every chat, without any API token or manual upload. The sidecar is idempotent: re-running `docker compose up` picks up content changes from the source files and UPSERTs them. The older `make deploy-functions` / `scripts/deploy_function.sh` flow still exists as a manual escape hatch for redeploying edited sources without a stack restart (requires `OWUI_ADMIN_TOKEN`).

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
- **Memory Service** (`memory-service/`) is a FastAPI microservice with pgvector. It calls LiteLLM for embeddings and for LLM-based fact/summary generation. Two parallel memory layers: **facts** (`memories` table, durable user traits, fed by `/memories/extract`) and **conversation episodes** (`conversation_episodes` table, per-dialog summary + 1024-dim embedding + `chat_id`/`message_indices` back-reference, fed by `/episodes`). Embeddings route through LiteLLM → `mws/bge-m3`; on any upstream failure `get_embedding` raises `EmbeddingError` — there is **no** hash fallback, corrupted vectors are considered worse than a hard failure. The outbound `/v1/embeddings` body must include `encoding_format: "float"` — without it MWS GPT upstream returns HTTP 400 (see phase-10-10).
- **OpenWebUI Functions** (filters) are managed via `/api/v1/functions/`. **Tools** use a separate API: `/api/v1/tools/`. The `mws_memory` filter is global and active. Tools `mws_memory_tool` and `mws_usage_stats` are available in chat. **Pipe functions** (3rd type) register as virtual models — `mws_auto_router` is the phase-9 auto-router that appears as "MWS GPT Auto 🎯" in the model dropdown; `mws_image_gen` exposes `mws/qwen-image` / `mws/qwen-image-lightning` as virtual chat models "MWS Image 🎨" / "MWS Image Lightning ⚡" so direct-select image generation routes through `/v1/images/generations` instead of the wrong `/v1/chat/completions` endpoint.
- **Auto-Router (phase-9)**: the "MWS GPT Auto 🎯" virtual model classifies each request (rules first, LLM fallback on `mws/gpt-oss-20b` with JSON mode) and dispatches to subagents in parallel via `asyncio.gather`. Each subagent is a fresh LiteLLM call whose output is compacted to a ≤500-token summary — the orchestrator never sees raw sub-responses (context isolation). Manual model picks from the dropdown bypass the router entirely. Design in `PLAN_chat_agents.md`, tasks in `tasks/phase-9-*.md`, model choices in `model_capabilities.md`.
- **Pipe functions and env vars**: OpenWebUI Pipe functions (like `auto_router_function.py`) run inside the `openwebui` container and read env via `os.getenv(...)`. The compose file **must** explicitly pass `LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}` to the `openwebui` service — otherwise Pipe calls back to LiteLLM return `401 Unauthorized` even though OpenWebUI itself works (it uses `OPENAI_API_KEYS` for its own requests, not for Pipe internals).
- **Modality routing (confirmed with MWS GPT API team, 2026-04-12)**: each upstream model is tied to exactly one endpoint and will return an error if called through the wrong one. Mapping: text/chat → `/v1/chat/completions`, embeddings → `/v1/embeddings`, image generation → `/v1/images/generations`, ASR/STT → `/v1/audio/transcriptions`. LiteLLM forwards by proxy endpoint, so as long as the caller hits the correct path (`client.embeddings.create` for `mws/bge-m3`, `client.audio.transcriptions.create` for `mws/whisper-*`, `client.images.generate` for `mws/qwen-image*`), the model works. The auto-router already does this: chat subagents use chat completions, `_sa_stt` POSTs multipart to `/audio/transcriptions`, `_sa_image_gen` POSTs to `/images/generations`, Memory Service POSTs to `/embeddings`. Previously suspected-broken models (`mws/qwen-image*`, `mws/qwen3-vl`, `mws/bge-gemma2`, `mws/qwen3-embedding`, `mws/whisper-medium`) are in fact working — earlier 404s came from calling them on the wrong modality.
- **Known-broken upstream model (as of 2026-04-12)**: `mws/t-pro` only — upstream rejects it with `Invalid model name passed in model=t-pro-it-1.0`. The auto-router RU aggregator and `_sa_ru_chat` use `mws/qwen3-235b` as a replacement. All other `mws/*` aliases are considered healthy when used on the correct endpoint.
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
- `memory-service/app/` — FastAPI app: `models.py` (SQLAlchemy+pgvector, incl. `ConversationEpisode`), `routers/memories.py` (facts CRUD+search+extract), `routers/episodes.py` (episodes write+recall), `embedding.py` (LiteLLM → `mws/bge-m3`, raises on failure), `episodes.py` (summary generation via `SUMMARY_MODEL`), `extraction.py`, `config.py`
- `tts-service/main.py` — gTTS-based OpenAI-compatible TTS endpoint
- `pipelines/memory_function.py` — OpenWebUI filter function source (deployed via API as `mws_memory`)
- `pipelines/memory_tool.py` — OpenWebUI Tool: view/search/delete user memories from chat
- `pipelines/usage_stats_tool.py` — OpenWebUI Tool: model usage and spend statistics
- `pipelines/auto_router_function.py` — **(phase-9, done)** OpenWebUI Pipe function "MWS GPT Auto 🎯": detects modality, classifies intent (rules → `mws/gpt-oss-20b` JSON fallback), dispatches 13 subagents in parallel with context isolation, streams the final aggregate answer. Deployed via `make deploy-functions`.
- `PLAN_chat_agents.md` — master design doc for the auto-router: architecture, subagents, feature mapping, verification scenarios. Source of truth for `tasks/phase-9-*.md`.
- `PLAN_db_memory.md` — **(phase-10, planned)** design doc for persistent conversation memory: `conversation_episodes` table in the `memory` DB (summary + embedding + chat_id ref, not raw Q/A), `/episodes` + `/episodes/recall` endpoints, `sa_memory_recall` subagent with `user_id` plumbing and `time_window` classifier intent. Source of truth for `tasks/phase-10-*.md`.
- `model_capabilities.md` — curated "task → best MWS model" map used by the auto-router classifier and documented for humans
- `openwebui/static/custom.css` — MWS custom theme (volume-mounted into container)
- `monitoring/prometheus.yml` — Prometheus scrape config
- `monitoring/grafana/` — provisioning + dashboards JSON
- `nginx/nginx.conf` — reverse proxy with rate limiting, security headers, attack path blocking, HTTPS-ready
- `scripts/init-databases.sql` — PostgreSQL multi-database initialization
- `scripts/bootstrap.py` — one-shot init sidecar: polls postgres for the first OpenWebUI user and UPSERTs `auto_router_function.py` + `memory_function.py` into the `function` table. Enables `docker compose up -d` to be the only command needed.
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
POST   /episodes              — write conversation episode (summary+embedding+chat_id ref)
POST   /episodes/recall       — semantic + time-window recall over episodes
GET    /health                — healthcheck
```

## Agent System

Development is organized through 15 specialized subagents defined in `.claude/agents.json` and described in `project_assignment.md`. The team lead (Opus) coordinates and delegates ALL implementation work to worker subagents (Sonnet). The main context is strictly for coordination, planning, and user communication — never for direct implementation.

Key agents: ArchitectAgent, DevOpsAgent, SecurityAgent, BackendCoderAgent, AIRouterAgent, MemoryAgent, VoiceAgent, FileAgent, FrontendAgent/OpenWebUIAgent, ReviewerAgent, TesterAgent, DocsAgent, SkillCreatorAgent. Each has mapped MCP skills in `.claude/skills/`.

## Project Status

All 8 phases of the initial build are **DONE** (completed 2026-03-28 — 2026-03-29). Reports in `tasks_done/phase-{1..8}-done.md`. The plan is in `PLAN.md`, detailed assignment in `project_assignment.md`.

**Post-Phase-8 migration (2026-04-10):** The stack was reworked to use a single provider — MWS GPT API — replacing the prior multi-provider setup (Anthropic, OpenAI direct, OpenRouter, DashScope). The smart `mws/auto` router was removed. RAG and STT were also migrated from local models (sentence-transformers, faster-whisper) to MWS GPT via LiteLLM to avoid HuggingFace downloads at first boot. Memory Service still has outdated default model names — see the warning above.

**Phase 9 — Auto-Router (done, 2026-04-11):** Shipped the "MWS GPT Auto 🎯" virtual model as a single-file OpenWebUI Pipe at `pipelines/auto_router_function.py`. Flow: rules-based `_detect` → hybrid `_classify_and_plan` (rules short-circuit → `mws/gpt-oss-20b` JSON fallback) → `asyncio.gather` across up to 4 subagents → streaming aggregator (`mws/qwen3-235b` RU / `mws/gpt-alpha` EN). 13 subagents implemented: `general`, `ru_chat` (`qwen3-235b`), `code` (`qwen3-coder`), `reasoner` (`deepseek-r1-32b`, strips CoT before `### Answer:`), `long_doc` (`glm-4.6`), `vision` (`cotype-pro-vl` RU / `qwen3-vl` EN, with auto-fallback to `cotype-pro-vl` if primary silently drops the image), `stt` (`whisper-turbo` via multipart, then re-classifies the transcript), `image_gen` (`qwen-image`, returns `artifacts`), `web_fetch` (httpx + `llama-3.1-8b`), `web_search` (DuckDuckGo HTML + `kimi-k2`), `doc_qa` (relies on OpenWebUI built-in RAG/BGE-M3 + `glm-4.6`), plus `deep_research` and `presentation` as v1 stubs. Context-isolation invariant: orchestrator only holds `CompactResult.summary` (≤500 tokens), never raw sub-responses. Deployment: zero-config via `bootstrap` sidecar; `scripts/deploy_function.sh` + `make deploy-functions` remain as a manual escape hatch. docker-compose adds `ENABLE_RAG_WEB_SEARCH=true`, `RAG_WEB_SEARCH_ENGINE=duckduckgo`. Report in `tasks_done/phase-9-done.md`.

**Phase 9 — post-launch fixes (2026-04-11, runtime verification):** During E2E smoke-testing in live OpenWebUI several issues were caught and fixed in-place (all changes in `pipelines/auto_router_function.py` + `docker-compose.yml`, picked up by `docker compose restart bootstrap`):

1. **401 Unauthorized from Pipe → LiteLLM.** Pipe reads `os.getenv("LITELLM_MASTER_KEY")` but the openwebui container only received `OPENAI_API_KEYS=${LITELLM_MASTER_KEY}`, not `LITELLM_MASTER_KEY` itself. **Fix:** added explicit `LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}` to the `openwebui` service env in `docker-compose.yml`.
2. **`mws/t-pro` unavailable upstream** (`Invalid model name passed in model=t-pro-it-1.0`). **Fix:** replaced as default RU aggregator and in `_sa_ru_chat` with `mws/qwen3-235b` (verified green). Classifier system prompt and examples updated.
3. **Classifier quality — RU chat routing.** `mws/gpt-oss-20b` JSON classifier frequently returns `intent="general"` for Russian casual text, which routed to `sa_general`/`gpt-alpha` instead of `sa_ru_chat`. **Fix (patch #1):** lang-aware override at the end of `_llm_classify` — if `detected.lang == "ru"` and `kind == "general"`, rewrite to `ru_chat` + `default_ru_model`.
4. **Classifier quality — formal proofs.** Math proofs ("докажи, что…", "prove that…") were classified as generic chat instead of reasoning, so `deepseek-r1-32b` CoT-stripping never kicked in. **Fix (patch #2):** `_REASONER_RE` regex (`докажи|доказательство|теорема|лемма|формально|prove|proof|theorem|lemma|formally` + symbols `∀∃∈⊂≡⇒⇔`) short-circuits to `sa_reasoner` before the LLM classifier runs.
5. **Classifier quality — long documents.** Long pasted text (meeting notes, transcripts) was routed to `sa_ru_chat` instead of `sa_long_doc`, losing `glm-4.6`'s long-context strength. **Fix (patch #3):** length-based short-circuit — `len(last_user_text) >= 1500` → `sa_long_doc` + `mws/glm-4.6`, placed **before** the reasoner regex with a `not _REASONER_RE.search(...)` guard so long formal proofs still reach the reasoner.

Smoke-test groups passed after fixes: **A (text/routing chat)** 6/6, **B (classifier incl. stubs)** 4/4. Remaining groups (C mulitmodal, D web, E memory, F manual override, G infra) not yet verified in this run.

**Postgres bind-mount migration (2026-04-11):** Switched `postgres_data` from a named Docker volume to a host bind-mount `./data/postgres`. All 4 databases (`openwebui`, `litellm`, `langfuse`, `memory`) now live on the host filesystem and survive `docker compose down -v` / container rebuilds. `.gitignore` excludes `data/`. One-off migration from the old named volume used `docker run --rm -v task-repo_postgres_data:/from -v "${PWD}/data/postgres:/to" alpine sh -c "cp -a /from/. /to/"`.

**Phase 10 — Persistent conversation memory (done, 2026-04-12):** Design in `PLAN_db_memory.md`, report in `tasks_done/phase-10-done.md`. Shipped: `conversation_episodes` table (summary + 1024-dim pgvector embedding + `chat_id`/`message_indices` back-reference — raw Q/A stays in `openwebui.chat`, not duplicated) with composite `ix_episodes_user_time` index and ivfflat cosine index on embedding; `POST /episodes` (generates summary via `SUMMARY_MODEL=mws/gpt-alpha`, embeds via `mws/bge-m3`, 400 on empty, 502 on LiteLLM failure — no silent inserts); `POST /episodes/recall` (raw-SQL pgvector cosine `<=>` with optional `date_from`/`date_to` window, `limit = min(N, 20)`, user-scoped); `sa_memory_recall` auto-router subagent; `user_id` plumbed from `pipe(__user__=...)` into every `SubTask.metadata`; classifier `memory_recall` intent with `time_window: {from, to}` ISO-8601 parsing relative to `Current date:` in system prompt; `_MEMORY_RECALL_RE` regex short-circuit (ordered BEFORE length-1500 long_doc and reasoner guards); `memory_function.outlet` now writes an episode per throttle tick alongside `/memories/extract`, errors swallowed. Facts and episodes are **parallel layers** — facts = "who the user is", episodes = "what we talked about N time ago".

**Phase 10 — post-launch fixes (2026-04-12):**
1. **phase-10-10 — embeddings `encoding_format` upstream incompatibility.** MWS GPT `/v1/embeddings` upstream rejects requests without an explicit `encoding_format: "float"` in the JSON body (HTTP 400 "expected value at line 1 column 67"). **Fix (v1):** added `"encoding_format": "float"` to `memory-service/app/embedding.py` request body. **Fix (v2, same session):** pushed the param into `litellm/config.yaml` via `extra_body: {encoding_format: "float"}` on all three embedding models (`mws/bge-m3`, `mws/bge-gemma2`, `mws/qwen3-embedding`). Now LiteLLM injects it into every proxy request, fixing both OpenWebUI RAG calls and Memory Service calls without client-side changes.
2. **OpenWebUI strips file attachments from pipe body.** Discovery: OpenWebUI provides `body["files"]` and `body["metadata"]` (with full file info incl. `content_type`, `path`, `id`) to **inlet filter** functions, but strips them before calling **pipe** functions — the pipe sees only `body = {model, messages, stream}`. Images are the exception: they're inlined as `image_url` content parts. Audio, documents, and other file types are never passed to the pipe. **Fix:** `memory_function.py` inlet now calls `_inject_file_tags()` which detects audio and document files from `body["metadata"]["parent_message"]["files"]` (current message only, not accumulated chat files) and injects `<mws_audio_files>[...]</mws_audio_files>` / `<mws_doc_files>[...]</mws_doc_files>` tags into the last user message text. The pipe's `_detect()` parses and strips these tags before lang detection, populating `has_audio`/`has_document` and `audio_attachments`/`document_attachments`. The `_sa_stt` subagent reads audio bytes from the local filesystem path (`/app/backend/data/uploads/...`) since both filter and pipe run in the same `openwebui` container.
3. **Vision — `mws/qwen2.5-vl-72b` silently drops images.** During group-C smoke tests the EN vision path (`"Describe this image in English"`) went to `mws/qwen2.5-vl-72b` and the model replied "I don't see an image provided" despite a correct multimodal payload. RU path via `mws/cotype-pro-vl` worked fine on the same image. **Fix:** EN vision default switched to `mws/qwen3-vl` (confirmed working by the API team); `_sa_vision` now detects blind responses via `_VISION_BLIND_RE` ("I don't see an image", "не вижу изображения", etc.) and auto-retries with `mws/cotype-pro-vl` as fallback, recording the actually-used model in `CompactResult.metadata.used`. `model_capabilities.md` matrix updated accordingly.
4. **Image generation via direct model pick (404).** Selecting `mws/qwen-image` directly from the OpenWebUI model dropdown returned `litellm.NotFoundError ... Received Model Group=mws/qwen-image` because OpenWebUI always POSTs to `/v1/chat/completions`, but `qwen-image` only lives on `/v1/images/generations`. Auto-router already worked around this via `_sa_image_gen`, but direct-pick chats were broken. **Fix:** added `pipelines/image_gen_function.py` — a new Pipe function that exposes two virtual chat models "MWS Image 🎨" and "MWS Image Lightning ⚡", extracts the last user message as prompt, and POSTs to `/v1/images/generations` with the matching `mws/qwen-image[-lightning]` upstream. Seeded via `bootstrap.py` as `mws_image_gen` (pipe, global, active). Raw LiteLLM aliases `mws/qwen-image*` are still required for the auto-router's `_sa_image_gen` path and remain in `litellm/config.yaml`; operators are expected to hide them from the picker via OpenWebUI Admin → Models.
5. **Classifier — semantic memory_recall.** Initial regex-only `_MEMORY_RECALL_RE` was too narrow (missed "о чём был разговор", "какая была тема позавчерашнего разговора" etc.) and `mws/gpt-oss-20b` classifier was ignoring the buried instruction. **Fix:** regex widened to cover "был разговор / шла речь / обсуждали / помнишь / вчера / позавчера / yesterday" family; classifier system prompt restructured with a **CRITICAL RULE — memory_recall** block up front, explicit "do NOT classify as ru_chat/general" guard, and three dated examples showing the model how to compute `time_window` for "вчера"/"позавчера"/"неделю назад" relative to the current date. Regex remains a cheap shortcut; the LLM classifier is the semantic primary path.
6. **STT re-plan language override.** After STT transcription, re-plan always re-detected language from the transcript content. When user sent English text + Russian audio (e.g. "Summarize this audio" + RU mp3), the re-plan overrode lang to `ru`, producing a Russian response. **Fix:** preserve the original request language (`detected.lang`) when the user wrote explicit text; only infer lang from transcript when the message was audio-only (no user text).
7. **Suppress `web_fetch` when `has_document=True`.** OpenWebUI's RAG injects document content (including URLs like GitHub links from a PDF) into the user message context. The URL detector counted these as user-intent URLs and triggered `web_fetch` alongside `doc_qa`. **Fix:** `web_fetch` is now gated with `not detected.has_document`. The `doc_qa` subagent also receives `doc_names` metadata with the current document filename and instructs glm-4.6 to focus only on that document, ignoring RAG-accumulated context from other files in the chat.

Smoke-test group **C (multimodal)** passed 5/5 after fixes: C1 vision RU, C2 vision EN, C3 audio STT (MP3+WAV), C4 audio+EN text, C5 document QA (PDF).

**Remote server deployment fixes (2026-04-12):**
1. **Nginx 500/429 on page load.** Browser fires ~20+ parallel requests for static assets (JS, CSS, fonts) on first load, exceeding `10r/s` + `burst=20` rate limit. Static assets hit 429, OpenWebUI returned 500. **Fix:** added a dedicated `location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot|map)$` block in `nginx/nginx.conf` with **no rate limit** (static files don't stress the backend); increased general `burst` from 20 to 40.
2. **CSP blocks generated images.** `img-src 'self' data: blob:` in nginx Content-Security-Policy blocked external image URLs (e.g. `https://imagegen.gpt.mws.ru/...`) returned by `_sa_image_gen`. **Fix:** added `https:` to `img-src` directive in both HTTP and HTTPS server blocks.
3. **Aggregator hallucinates image URLs.** The final aggregator LLM inserted its own `![alt](fake_url)` markdown image links alongside the real image from `_render_artifacts`, producing broken image icons in chat. Prompt-level instruction ("don't insert image links") was unreliable. **Fix:** when `has_artifacts` is true, the aggregator output is buffered (not streamed) and `re.sub(r"!\[[^\]]*\]\([^\)]+\)", "", text)` strips all markdown image links before yielding; real images are appended after via `_render_artifacts` as before. When no artifacts, streaming is preserved unchanged. Verified locally via API test — only the real `![generated](url)` from `_render_artifacts` remains.
