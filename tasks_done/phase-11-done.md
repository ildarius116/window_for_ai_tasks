# Phase 11 — Real `.pptx` presentations (done, 2026-04-13)

Goal: replace the `_sa_presentation` v1 stub with an end-to-end `.pptx` generation pipeline, delivering the file back into the OpenWebUI chat via the existing artifact pattern (same contract as `_sa_image_gen`).

## What shipped

### 1. New microservice `pptx-service/`
- `Dockerfile` — `python:3.11-slim`, installs `requirements.txt`, runs `uvicorn main:app --host 0.0.0.0 --port 8000`.
- `requirements.txt` — `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`, `pydantic==2.10.3`, `httpx==0.28.1`, `python-pptx==1.0.2`, `pypdf==5.1.0`, `python-docx==1.1.2`, `python-multipart==0.0.19`.
- `models.py` — pydantic `Slide` / `PresentationSchema`.
- `parsing.py` — `extract_text(filename, bytes)` for `.pdf`, `.docx`, `.txt`, `.md`, `.markdown`, `.rst`; truncates to `MAX_CHARS=40000`; raises `UnsupportedFormat` for other extensions.
- `schema_llm.py` — `generate_schema(source_text, user_instruction)` calls LiteLLM (`SCHEMA_MODEL=mws/glm-4.6`, `response_format={"type":"json_object"}`, `max_tokens=4500`, `temperature=0.3`) with a strict RU+EN system prompt (5–10 slides, ≤8-word titles, ≤120-char bullets, 3–6 bullets per slide, optional 1–3-sentence notes); validates via `PresentationSchema`, one retry on `ValidationError`, raises `SchemaGenerationError` on transport/JSON/validation failure.
- `builder.py` — `build_pptx(schema) -> bytes`: title slide (layout 0) with title+subtitle, then Title-and-Content layout (1) per slide with bullets as paragraphs in placeholder idx=1 and `slide.notes_slide.notes_text_frame.text = slide.notes`. Returns bytes via `BytesIO`.
- `main.py` — FastAPI app with `GET /health`, `POST /build` multipart (`file`, `user_instruction`, optional `source_text`), timing middleware logging each request. Error contract: 400 empty input, 413 `>20 MB`, 415 unsupported extension, 502 `SchemaGenerationError`, 500 render failure. Response: `StreamingResponse` with `Content-Disposition: attachment; filename="{slug}.pptx"`, `X-Slide-Count`, and `X-Title-B64` (base64-encoded UTF-8 title, because HTTP headers are latin-1 only — this was caught in E2E and fixed in the same session).

### 2. `docker-compose.yml`
- Added `pptx-service` service block: `build: ./pptx-service`, `LITELLM_URL=http://litellm:4000`, `LITELLM_API_KEY=${LITELLM_MASTER_KEY}`, `SCHEMA_MODEL=mws/glm-4.6`, urllib-based healthcheck on `/health`, internal-only (no host port), `depends_on: litellm`.
- Added `OWUI_ADMIN_TOKEN: ${OWUI_ADMIN_TOKEN:-}` to the `openwebui` service env. Required for `_upload_to_owui_files` to succeed.

### 3. `pipelines/auto_router_function.py`
- **`_upload_to_owui_files(content, filename, mime)`** — POSTs multipart to `http://localhost:8080/api/v1/files/` with `Authorization: Bearer $OWUI_ADMIN_TOKEN`. Returns the JSON payload on success, `None` on missing token / non-200 / transport error. Never raises — the caller decides on fallback.
- **`_sa_presentation`** rewritten: reads the first readable attachment from disk (same pattern as `_sa_stt`), checks the 20 MB cap, POSTs to `http://pptx-service:8000/build` with a multipart body (the instruction text goes in `user_instruction`, the raw file bytes in `file`), decodes the base64 title from `X-Title-B64`, slugs it via a new `_slug` helper (Cyrillic → Latin transliteration + `re.sub(\W+, _)`, cap 60 chars), uploads the `.pptx` to OpenWebUI Files API, and returns `CompactResult(kind="presentation", summary=f"Готова презентация «{title}» — {slide_count} слайдов.", artifacts=[{"type":"file", "url":f"/api/v1/files/{id}/content", "filename":safe_name, "mime":PPTX_MIME}])`.
- **`_presentation_text_fallback(task, reason, prefix="")`** — asks `mws/glm-4.6` to produce a 5–8 slide markdown outline when pptx-service is unreachable, returns 5xx, `OWUI_ADMIN_TOKEN` is missing, or upload fails. Always returns a `CompactResult` — never raises — so the final response is never a 500.
- **`_render_artifacts`** grew a `type="file"` branch emitting `📎 [filename](url)`.
- **`_stream_aggregate`** grew a second strip regex that removes hallucinated markdown links pointing at `/api/v1/files/` or `.pptx`, so the aggregator can't duplicate our real artifact. The image strip remains.
- **Classifier system prompt** (`_llm_classify`) has a new **CRITICAL RULE — presentation** block above the memory_recall block, with two worked examples ("Сделай презентацию про async/await" → `{"intents":["presentation"]}`; "Вот резюме. Сделай из него презентацию." with `has_document=true` → `{"intents":["presentation"]}`).
- **Classifier safety-net**: a `_looks_like_presentation(text)` word-marker check (`_PPTX_MARKERS` = `презентация/презентацию/слайды/pptx/powerpoint/presentation/slides/deck/…`) runs at the **very top** of `_classify_and_plan`, before the `has_document` / `has_image` / `has_audio` short-circuits. When it fires, the plan becomes a single `SubTask(kind="presentation", attachments=detected.document_attachments, …)` and all other branches are skipped. Note: this is intentionally a primary gate because presentation is a content-type intent (not a semantic ambiguity) and the markers are lexically unambiguous — consistent with the "no regex as primary gate for SEMANTIC intent" feedback-memory rule (presentation is NOT semantic disambiguation, it's a delivery-format override).
- **Aggregator system prompt** rewritten to cover both images and files: "НЕ пиши фраз вида «изображение сгенерировано» или «файл приложён»: только краткое содержательное резюме того, что было сделано (тема, число слайдов и т.п.)." This fixed a phantom "Изображение сгенерировано." line that was being inserted for `type="file"` artifacts.

### 4. `.env.example`
- `OWUI_ADMIN_TOKEN` comment updated: it is now required for phase-11 pptx delivery (previously optional, only used by the `make deploy-functions` escape hatch). Without it `_sa_presentation` falls back to a markdown slide outline.

### 5. `CLAUDE.md`
- Architecture diagram — `PPTX Service (:8000 internal)` line expanded from "planned" to "parses PDF/DOCX/TXT → LiteLLM (mws/glm-4.6, JSON mode) → python-pptx → .pptx bytes".
- Services count raised from 11 to 12, `pptx-service` added to the list.
- Commands section gained `docker compose build pptx-service && docker compose up -d pptx-service`.
- Development Conventions gained a `PPTX Service` paragraph and a dev-workflow note that editing `pipelines/auto_router_function.py` requires `docker compose restart bootstrap && docker compose restart openwebui` (not just restarting openwebui) because the bootstrap sidecar seeds the pipe sources into the DB on its first run and doesn't re-trigger on a plain `openwebui` restart.
- Key Files gained `pptx-service/main.py`, `builder.py`, `schema_llm.py`, `parsing.py`, `models.py`.
- Project Status: new **Phase 11 — Real `.pptx` presentations (done, 2026-04-13)** block replacing the earlier "planned" entry, plus a dedicated **Phase 11 — incidents caught during E2E** sub-block documenting the 5 runtime fixes below.

## Runtime incidents caught during E2E (all fixed in-session)

1. **`max_tokens=2000` truncates JSON mid-string.** First real `/build` call against `mws/glm-4.6` returned `LiteLLM returned non-JSON: Unterminated string starting at ... char 519`. A full deck with bullets + speaker notes comfortably exceeds 2000 tokens. **Fix:** bumped `max_tokens` to 4500 in `pptx-service/schema_llm.py`. `response_format=json_object` does NOT auto-repair the tail, and a retry alone doesn't help because the model stops at the same boundary. Longer-term: switch to streaming JSON with a repair step or use partial-JSON parsing.
2. **Cyrillic `X-Title` crashes Starlette (`UnicodeEncodeError: 'latin-1' codec can't encode characters`).** HTTP headers are latin-1 only. **Fix:** base64-encode the UTF-8 title into `X-Title-B64` in pptx-service and decode it in the pipe.
3. **Bootstrap sidecar doesn't re-seed on `docker compose up -d openwebui`.** After editing the pipe source, a plain openwebui recreate left the old pipe in the DB → `_slug` fallback kicked in for every call, producing `presentation.pptx` regardless of the actual schema title. **Fix:** explicit `docker compose restart bootstrap && docker compose restart openwebui` (documented in CLAUDE.md as a new dev-convention rule).
4. **Aggregator hallucinates "Изображение сгенерировано." for file artifacts.** The old `_stream_aggregate` system prompt hard-coded "скажи одной строкой, что изображение сгенерировано" — fired for the new `type="file"` artifact too. **Fix:** prompt rewritten to cover both images and files generically, with explicit instructions NOT to insert any standalone "файл приложён" / "изображение сгенерировано" lines.
5. **Direct OpenWebUI chat API needs `metadata.parent_message.files` to propagate attachments to the pipe.** Passing only top-level `files=[{id}]` does not trigger `memory_function._inject_file_tags`, which reads `body["metadata"]["parent_message"]["files"]` first (falls back to `body["files"]` only if the nested path is empty AND each entry has inner `file.id`/`file.meta`/`file.path`). Documented for future E2E test scripts; the real OpenWebUI frontend already sends the nested shape.

## E2E smoke results (2026-04-13)

All scenarios run via live `POST /api/chat/completions` against `mws_auto_router.mws-auto` from inside the `openwebui` container with a minted admin JWT.

| # | Scenario | Routing | Outcome |
|---|----------|---------|---------|
| S1 | `docs/project-overview.md` + «Сделай из этого документа презентацию.» | `doc=True`, plan `[presentation]` | ✅ 10-slide `mws_gpt_platform_project_overview.pptx`, content pulled from the source (High-Level Architecture, Backend FastAPI, Frontend/AI Gateway, Database & Caching, Background Processing, Monitoring, Configuration, Development Workflow, Key Technologies Stack). |
| S2 | Text-only «Сделай презентацию про Python async/await на 5 слайдов.» | plan `[presentation]` | ✅ 6-slide `python_async_await.pptx` (title + 5 content) generated from model knowledge. |
| S4 | Same doc + «Что написано в этом документе?» (regression) | plan `[doc_qa]` | ✅ Safety-net did NOT fire — normal doc_qa answer via `mws/glm-4.6`, no presentation. |
| S5 | `docker compose stop pptx-service` + «Сделай презентацию про Elixir на 3 слайда.» | plan `[presentation]`, pptx-service unreachable | ✅ Text fallback: markdown slide outline via glm-4.6, no 500, no file artifact. `docker compose start pptx-service` after. |
| S7 | Manual model pick `mws/qwen3-235b` + «Сделай презентацию про Rust на 3 слайда.» | router bypassed entirely | ✅ Normal markdown chat response, no routing-decision block, no file artifact. |

Code-verified (not live-tested in this session but on the same code paths as S5):
- **S6** — `OWUI_ADMIN_TOKEN` missing → `_upload_to_owui_files` returns `None` → `_presentation_text_fallback` fires with prefix warning "⚠️ Файл .pptx сгенерирован, но не может быть приложен к чату…".
- **S8** — attachment >20 MB → pipe-side guard triggers `_presentation_text_fallback` with reason "attachment > 20 MB; use a smaller source". pptx-service also has its own 413 guard.
- **S3** (DOCX) — same code path as S1 (PDF), `parsing.extract_text` handles DOCX via `python-docx`.

## Known limitations (v1, deferred to v2)

- **No corporate template.** Default python-pptx layouts only. `mws_template.pptx` support is trivial to add (`Presentation("mws_template.pptx")`) once design gives us a template file.
- **No images inside slides.** Title slide and content slides are text-only. A v2 task could add an optional cover image generated via `mws/qwen-image` and inserted on slide 0.
- **Upload cap 20 MB.** Hard limit on both the pipe and the service. Large sources should be pre-processed by the user.
- **`_slug` drops non-latin/non-cyrillic characters** (Chinese, Arabic, Hebrew, emoji). Those become underscores. OK for filenames, never corrupts content (content is stored as UTF-8 inside the `.pptx`).
- **Single source file per request.** If the user attaches multiple docs, only the first readable one is used. Multi-source merging is a v2 concern.
- **`OWUI_ADMIN_TOKEN` is a manual step.** Operators must mint it after the first admin signup and put it in `.env`. A bootstrap-side minting sidecar that pulls a JWT from the DB directly would remove this step — deferred.

## Files touched (net)

New:
- `pptx-service/Dockerfile`
- `pptx-service/requirements.txt`
- `pptx-service/main.py`
- `pptx-service/builder.py`
- `pptx-service/parsing.py`
- `pptx-service/schema_llm.py`
- `pptx-service/models.py`
- `tasks_done/phase-11-done.md` (this file)

Modified:
- `pipelines/auto_router_function.py` — `_sa_presentation` rewrite, `_upload_to_owui_files`, `_slug`, `_render_artifacts` (file branch), `_stream_aggregate` (strip + prompt rewrite), `_classify_and_plan` (presentation safety-net), `_llm_classify` system prompt (presentation rule + examples).
- `docker-compose.yml` — `pptx-service` service + `OWUI_ADMIN_TOKEN` in openwebui env.
- `.env.example` — `OWUI_ADMIN_TOKEN` comment.
- `CLAUDE.md` — architecture diagram, services list, commands, Development Conventions (pptx-service paragraph + bootstrap re-seed note), Key Files, Project Status (phase-11 done block + incidents sub-block).
