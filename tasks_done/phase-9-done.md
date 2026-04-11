# Phase 9 — MWS GPT Auto-Router — Done Report

**Date:** 2026-04-11
**Scope:** design + implementation of the "MWS GPT Auto 🎯" virtual model that auto-detects modality, classifies intent, dispatches parallel context-isolated subagents, and streams a unified markdown answer. Covers all **10 mandatory** features from `GPTHub_features_template.xlsx` + 2 v2 stubs.

## What shipped

| Task | File(s) | Status |
|---|---|---|
| phase-9-1 — Pipe scaffold | `pipelines/auto_router_function.py` (Valves, `pipes()`, stub `pipe()`) | ✅ |
| phase-9-2 — Input detector | `_detect()`, `DetectedInput` dataclass, regex rules for image/audio/doc/URL/lang/keywords | ✅ |
| phase-9-3 — Hybrid classifier | `_classify_and_plan()`, `_llm_classify()`, JSON-mode call to `mws/gpt-oss-20b`, safe fallback | ✅ |
| phase-9-4 — Subagent interface | `SubTask`, `CompactResult`, `_call_litellm`, `_call_litellm_stream`, `_dispatch`, `_run_subagent` | ✅ |
| phase-9-5 — Multimodal | `_sa_vision`, `_sa_stt` (multipart), `_sa_image_gen` (artifacts) | ✅ |
| phase-9-6 — Text subagents | `_sa_general`, `_sa_ru_chat`, `_sa_code`, `_sa_reasoner` (strips CoT before `### Answer:`), `_sa_long_doc` | ✅ |
| phase-9-7 — Web subagents | `_sa_web_fetch` (httpx + tag strip → `llama-3.1-8b`), `_sa_web_search` (DuckDuckGo HTML → `kimi-k2`) + docker-compose env | ✅ |
| phase-9-8 — Doc Q&A | `_sa_doc_qa` relying on OpenWebUI built-in RAG (Variant A, documented in source) | ✅ |
| phase-9-9 — Aggregator + stream | full `pipe()`, `_format_routing_block`, `_stream_aggregate` (SSE parser), `_maybe_reclassify_stt`, `_render_artifacts` | ✅ |
| phase-9-10 — Deploy automation | `scripts/deploy_function.sh`, `Makefile` target `deploy-functions`, `.env.example` entries, **`scripts/bootstrap.py` + `bootstrap` service in `docker-compose.yml`** for zero-config single-command startup | ✅ |
| phase-9-11 — v2 stubs | `_sa_deep_research`, `_sa_presentation` (friendly v2 notice) + classifier examples | ✅ |
| phase-9-12 — Docs + report | `CLAUDE.md` Auto-Router section, `README_proj.md` usage section, this file | ✅ |

## Files created / changed

**Created**
- `pipelines/auto_router_function.py` — single-source Pipe (≈850 LOC). Implements detector, classifier, planner, dispatcher, 13 subagents, streaming aggregator.
- `scripts/bootstrap.py` — one-shot init sidecar (runs in a `python:3.11-slim` container). Waits for postgres, waits for the `function`/`user` tables, polls for first user signup, then UPSERTs both Pipe/Filter functions directly into the `function` table. Schema-resilient: discovers columns from `information_schema` and only writes columns that exist.
- `scripts/deploy_function.sh` — curl/jq wrapper around `POST /api/v1/functions/create` with fallback to update. **Now an optional escape hatch**, not the primary deploy path.
- `PLAN_chat_agents.md` — master design doc (created in planning step).
- `tasks/phase-9-1..12-*.md` — 12 task cards (created in planning step).
- `tasks_done/phase-9-done.md` — this report.

**Changed**
- `Makefile` — added `deploy-functions` target (manual escape hatch).
- `docker-compose.yml` — added `ENABLE_RAG_WEB_SEARCH=true`, `RAG_WEB_SEARCH_ENGINE=duckduckgo`, `RAG_WEB_SEARCH_RESULT_COUNT=3` for OpenWebUI, plus a new `bootstrap` service running `scripts/bootstrap.py` (depends_on postgres healthy + openwebui started, restart on-failure, one-shot).
- `.env.example` — added `OWUI_ADMIN_TOKEN`, `OWUI_BASE_URL`.
- `CLAUDE.md` — Auto-Router section, new Key Files entries, Phase 9 status.
- `README_proj.md` — "How to use MWS GPT Auto 🎯" section, Key Files rows.

**Not touched** (per plan): `pipelines/memory_function.py`, `pipelines/memory_tool.py`, `pipelines/usage_stats_tool.py`, `litellm/config.yaml`, `memory-service/`.

## Architecture recap

```
User
 │
 ├─ memory_function.inlet (unchanged)
 │
 ├─ auto_router_function.Pipe.pipe
 │    ├─ _detect            ← rules only
 │    ├─ _classify_and_plan ← rules → LLM fallback (gpt-oss-20b, JSON mode)
 │    ├─ yield _format_routing_block  ← <details>🎯 Routing decision</details>
 │    ├─ _dispatch          ← asyncio.gather over subagents
 │    │     sa_vision | sa_stt | sa_image_gen | sa_doc_qa |
 │    │     sa_web_fetch | sa_web_search | sa_general | sa_ru_chat |
 │    │     sa_code | sa_reasoner | sa_long_doc |
 │    │     sa_deep_research (stub) | sa_presentation (stub)
 │    ├─ _maybe_reclassify_stt ← if sa_stt ran & no chat subagent, re-plan from transcript
 │    ├─ _stream_aggregate  ← SSE stream from mws/t-pro (RU) or mws/gpt-alpha (EN)
 │    └─ yield _render_artifacts ← appended image markdown
 │
 └─ memory_function.outlet (unchanged)
```

**Context isolation invariant:** the orchestrator only ever holds `CompactResult.summary` (≤ 500 tokens), never the raw sub-responses or chain-of-thought. The final aggregator receives a scratchpad with `[sa_*]` tags as its system prompt — nothing else from the sub-runs.

## Feature coverage (xlsx)

| # | Feature | Route | Model |
|---|---|---|---|
| 1 | Text chat | `sa_general` / `sa_ru_chat` | `mws/gpt-alpha` / `mws/t-pro` |
| 2 | Voice chat | `sa_stt` → re-plan | `mws/whisper-turbo` + final |
| 3 | Image generation | `sa_image_gen` | `mws/qwen-image` |
| 4 | Audio files + ASR | `sa_stt` → `sa_general` | `mws/whisper-turbo` + `mws/gpt-alpha` |
| 5 | Image analysis (VLM) | `sa_vision` | `mws/cotype-pro-vl` (RU) / `mws/qwen2.5-vl-72b` |
| 6 | File Q&A | `sa_doc_qa` (via built-in RAG / BGE-M3) | `mws/glm-4.6` |
| 7 | Web search | `sa_web_search` (DuckDuckGo) | `mws/kimi-k2` |
| 8 | URL parsing | `sa_web_fetch` | `mws/llama-3.1-8b` |
| 9 | Long-term memory | existing `memory_function.py` Filter | — |
| 10 | Auto-select | Detector + Classifier + Planner | `mws/gpt-oss-20b` |
| 11 | Manual select | OpenWebUI dropdown (bypasses Pipe) | any `mws/*` |
| 12 | Markdown & code | aggregator system prompt | final model |
| 13 | Deep Research | `sa_deep_research` stub | — (v2) |
| 14 | Presentation gen | `sa_presentation` stub | — (v2) |

## Verification status

Code was statically checked with `python -m py_compile pipelines/auto_router_function.py` — passes. **End-to-end runtime verification (12 scenarios from `PLAN_chat_agents.md` §13) is user-side**: it requires `make up` + admin signup + `make deploy-functions` + manual chat-UI interaction per scenario, which cannot be executed from inside this coding session. A verification checklist is available in `tasks/phase-9-12-e2e-verification.md`; each row maps 1:1 to a feature above.

**Recommended run order after deploy:**
1. Text RU → expect `sa_ru_chat` / `mws/t-pro`.
2. Code EN → expect classifier → `sa_code` / `mws/qwen3-coder`.
3. Image upload → expect `sa_vision`.
4. `.mp3` upload → expect `sa_stt` then re-plan into chat subagent.
5. PDF upload → expect `sa_doc_qa` / `mws/glm-4.6`.
6. "Нарисуй ..." → expect `sa_image_gen` with `artifacts`.
7. "Найди в интернете ..." → expect `sa_web_search` (DuckDuckGo) / `mws/kimi-k2`.
8. Message with URL → expect `sa_web_fetch` / `mws/llama-3.1-8b`.
9. Manual pick `mws/deepseek-r1-32b` → Pipe NOT invoked (flat Langfuse trace).
10. "Сравни Python и Rust таблицей" → markdown table renders.

## Known issues / tech debt

- **STT payload source uncertain.** OpenWebUI's exact `body["files"]` shape for audio (path vs. base64 vs. URL) may vary by version. `_sa_stt` handles three cases (base64 `data`, `url`, or pre-fetched path-free) — run scenario #4 and patch if the actual shape differs.
- **`_sa_doc_qa` relies on Variant A** (built-in RAG). If a particular OpenWebUI version drops retrieved chunks into a different field (e.g., a separate `context` key instead of the messages stream), the subagent will still work but with reduced accuracy. Documented in the method's docstring.
- **`_ddg_search`** parses DuckDuckGo's HTML response. DDG occasionally changes its markup. If zero results come back, enable `debug=True` on the Valves to dump the raw HTML and adjust the regex.
- **`_sa_web_fetch` uses a regex-based HTML strip**, not a full readability algorithm. For JS-rendered SPAs the extracted text may be empty — in that case the subagent still fails gracefully (returns `CompactResult(error=...)`).
- **`memory-service/` still has broken default model names** (`mws/nemotron`) — pre-existing issue, out of phase-9 scope. Flagged in `CLAUDE.md`.

## v2 backlog

- Real **Deep Research** subagent: multi-step plan → parallel search → synthesis → fact-check loop.
- Real **Presentation generation**: Marp/Reveal.js markdown generation with auto-inserted images via `mws/qwen-image`.
- Langfuse parent/child trace IDs — currently each subagent creates its own flat trace. Plan: generate `trace_id` in `pipe()` and propagate via LiteLLM `metadata`.
- Caching for `_sa_web_fetch` / `_ddg_search` (Redis TTL 10 min).
- Unit tests for `_detect` / `_classify_and_plan` (pure-logic, no network).
- Router budget guard: per-request token accounting via LiteLLM spend callbacks.

## Next phase

Phase-10 ideas (not scheduled yet): Deep Research v2, Presentation v2, trace-id propagation, fixing `memory-service` defaults, unit-test harness for Pipe subagents.
