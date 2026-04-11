# Task: phase-9-5 — Multimodal Subagents (vision, stt, image-gen)

## Goal
Реализовать три субагента, работающих с не-текстовыми модальностями: `sa_vision` (анализ изображений), `sa_stt` (распознавание аудио), `sa_image_gen` (генерация изображений).

## Context
См. `PLAN_chat_agents.md` раздел 7, строки sa_vision/sa_stt/sa_image_gen. Модели выбираются по `model_capabilities.md`.

## Scope

### `_sa_vision(task)`
- Модель: `mws/cotype-pro-vl` если в metadata `lang=="ru"` ИЛИ в image-attachments явно русский корпоративный документ (OCR задача); иначе `mws/qwen2.5-vl-72b`.
- Делает `chat/completions` с messages в OpenAI vision формате: `content = [{"type": "text", "text": task.input_text}, {"type": "image_url", "image_url": {"url": <base64 или URL из attachment>}}]`.
- Если attachments несколько — batched vision в одном сообщении.
- `max_tokens=400`.
- Summary = `response.choices[0].message.content` (усечение до 500 токенов через простую отрезку по длине).

### `_sa_stt(task)`
- Endpoint: `POST {base_url}/audio/transcriptions` с `multipart/form-data`.
- Body: `file=<audio bytes>`, `model=mws/whisper-turbo`, `language=ru` если доступно.
- `summary = transcript.strip()`.
- Если транскрипт > 500 токенов — truncate с пометкой `... [обрезано]`.
- `metadata["full_transcript"] = transcript` (для реклассификации в `_classify_and_plan`).

### `_sa_image_gen(task)`
- Endpoint: `POST {base_url}/images/generations`.
- Body: `{"model": "mws/qwen-image", "prompt": task.input_text, "n": 1, "size": "1024x1024"}`.
- Ответ содержит URL (или base64). Возвращаем `CompactResult(summary=f"Generated image for: {task.input_text}", artifacts=[{"type":"image","url": <url>}])`.

## Files
- `pipelines/auto_router_function.py` (изменить — заменить stub'ы этих трёх методов на реальные).

## Acceptance criteria
1. Загрузить PNG, отправить "что на картинке?" → `sa_vision` делает POST в LiteLLM → возвращает `CompactResult` с summary.
2. Загрузить короткий `.mp3` → `sa_stt` возвращает транскрипт в `summary`.
3. Попросить "нарисуй логотип кота" → `sa_image_gen` возвращает `CompactResult` с `artifacts=[{"type":"image","url":"..."}]`.
4. На сетевой ошибке LiteLLM — субагент возвращает `CompactResult(error=...)`, pipe() не падает.

## Dependencies
- phase-9-4 (интерфейс + `_call_litellm`).

## Out of scope
- Sticher для `artifacts` в финальный markdown — в phase-9-9.
- Рекласс транскрипта после `sa_stt` (из metadata) — в phase-9-9.
