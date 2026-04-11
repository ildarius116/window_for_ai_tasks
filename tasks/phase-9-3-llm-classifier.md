# Task: phase-9-3 — LLM Classifier with JSON mode

## Goal
Добавить классификатор, который либо short-circuit'ит на основе `DetectedInput` (правила), либо при неоднозначном тексте делает один дешёвый вызов `mws/gpt-oss-20b` с `response_format=json_object` и возвращает структурированный план.

## Context
См. `PLAN_chat_agents.md` раздел 5.2 и 5.3. Hybrid-подход: правила покрывают большинство кейсов, LLM нужен только для текстовых запросов без явных сигналов.

## Scope
- Метод `async def _classify_and_plan(self, detected: DetectedInput, messages: list) -> list[SubTask]`.
- Сначала — short-circuit:
  - `has_image` → SubTask(`vision`, model по `detected.lang`)
  - `has_audio` → SubTask(`stt`)
  - `has_document` → SubTask(`doc_qa`, model=`mws/glm-4.6`)
  - `urls` → SubTask(`web_fetch` per URL)
  - `wants_image_gen` → SubTask(`image_gen`)
  - `wants_web_search` → SubTask(`web_search`)
  - Если после этого shortlist не пуст — вернуть его (без LLM-вызова), добавив финальный `general`/`ru_chat` SubTask для агрегации, если в shortlist нет chat-subagent.
- Если shortlist пуст (чистый текст без сигналов) — LLM-call:
  - `POST {base_url}/chat/completions` c `model=mws/gpt-oss-20b`, `response_format={"type":"json_object"}`, `max_tokens=200`, `temperature=0`.
  - System prompt: "You are a router. Classify the user request and return JSON with fields: intents (list), lang, complexity, primary_model, reason. Valid intents: code, math, ru_chat, general, long_doc, agentic, deep_research, presentation. Valid models: mws/gpt-alpha, mws/t-pro, mws/qwen3-coder, mws/deepseek-r1-32b, mws/glm-4.6, mws/kimi-k2, mws/llama-3.1-8b."
  - Parse JSON. При ошибке парсинга → fallback `mws/t-pro` для `lang=ru`, иначе `mws/gpt-alpha`.
- Результат — `list[SubTask]` (1 или больше).
- Guard: максимум 4 элемента в plan.
- Логирование в `self.valves.debug`: dump `detected`, `classifier_output`, `plan`.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. При `detected.has_image=True` метод возвращает plan с `SubTask(kind="vision", ...)` БЕЗ LLM-вызова.
2. При чистом тексте "напиши функцию на python" — делается 1 LLM-call, plan содержит `SubTask(kind="code", model="mws/qwen3-coder")` или эквивалент.
3. При невалидном JSON-ответе classifier — plan имеет 1 элемент `SubTask(kind="ru_chat", model="mws/t-pro")` (если lang=ru) без падения.
4. Максимум 4 SubTask'а в plan при любых входах.
5. Метод асинхронный, использует `aiohttp` или `httpx.AsyncClient` (не `requests`).

## Dependencies
- phase-9-2 (DetectedInput).
- phase-9-4 (SubTask dataclass) — может делаться параллельно.

## Out of scope
- Реальные вызовы субагентов (phase-9-5..9-8, 9-11).
- Агрегация (phase-9-9).
