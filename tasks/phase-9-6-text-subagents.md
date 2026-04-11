# Task: phase-9-6 — Text Subagents (general, ru_chat, code, reasoner, long_doc)

## Goal
Реализовать 5 текстовых субагентов, каждый со своей моделью из `model_capabilities.md`.

## Context
См. `PLAN_chat_agents.md` раздел 7 и `model_capabilities.md` таблицу "Быстрый роутинг".

## Scope

Все 5 субагентов используют общий шаблон: берут `task.input_text` (или исходные `messages` из metadata) и делают один `chat/completions` вызов к LiteLLM, возвращая `CompactResult.summary = response_text[:500_tokens_approx]`.

Общая функция `_text_subagent(model, system, task)`, которую оборачивают 5 методов:

| Метод | Модель | System prompt (кратко) |
|---|---|---|
| `_sa_general` | `mws/gpt-alpha` | "You are a helpful assistant. Answer in English." |
| `_sa_ru_chat` | `mws/t-pro` | "Ты — дружелюбный ассистент. Отвечай на русском." |
| `_sa_code` | `mws/qwen3-coder` | "You are an expert software engineer. Produce clean, idiomatic code with brief explanation. Use markdown code blocks." |
| `_sa_reasoner` | `mws/deepseek-r1-32b` | "You are a careful reasoner. Think step-by-step, then give a concise final answer." + инструкция "After thinking, write the final answer after `### Answer:`". |
| `_sa_long_doc` | `mws/glm-4.6` | "You analyze long documents. Be precise and cite sections." |

Параметры вызова:
- `max_tokens = task.max_output_tokens or 400`.
- `temperature = 0.3` для code/reasoner, `0.7` для ru_chat/general/long_doc.
- Для reasoner'а: после получения ответа извлечь часть после `### Answer:` если есть — в summary. Полный reasoning выбрасывается (критично для изоляции контекста).

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. Вызвать `_sa_code` с `input_text="write fibonacci in rust"` → `CompactResult.summary` содержит markdown code block.
2. Вызвать `_sa_reasoner` с математической задачей → `summary` содержит только финальный ответ, без chain-of-thought.
3. Вызвать `_sa_ru_chat` с "Привет, как дела?" → ответ на русском.
4. Все 5 методов обрабатывают ошибки LiteLLM через общий `_call_litellm` (не падают).

## Dependencies
- phase-9-4 (интерфейс).

## Out of scope
- Детекция, какой из text-субагентов вызывать — это делает classifier в phase-9-3.
- Аggregate по результатам — phase-9-9.
