# Task: phase-9-9 — Aggregator + Streaming Response

## Goal
Связать всю pipeline: detector → classifier → dispatcher → aggregator → stream. Реализовать финальную сборку ответа через async generator и добавить collapsible `<details>` блок с routing decision в начале.

## Context
Ключевая задача v1: финальная модель видит ТОЛЬКО `CompactResult.summary` субагентов, не их chain-of-thought. См. `PLAN_chat_agents.md` разделы 3, 9, зафиксированное решение №4 (collapsible thinking).

## Scope

### 1. Полная реализация `pipe()`
```python
async def pipe(self, body, __user__=None, __request__=None):
    messages = body.get("messages", [])
    files = body.get("files", [])

    detected = self._detect(messages, files)
    plan = await self._classify_and_plan(detected, messages)

    # Collapsible routing block
    yield self._format_routing_block(plan, detected)

    # Parallel subagent execution
    results = await self._dispatch(plan)

    # Post-processing: если был sa_stt — реклассифицировать транскрипт
    results = await self._maybe_reclassify_stt(results, detected, messages)

    # Final aggregate model
    final_model = "mws/t-pro" if detected.lang == "ru" else "mws/gpt-alpha"

    async for chunk in self._stream_aggregate(final_model, messages, results):
        yield chunk
```

### 2. `_format_routing_block(plan, detected)`
Возвращает строку:
```
<details>
<summary>🎯 Routing decision</summary>

- **Lang:** {detected.lang}
- **Subagents:** {[t.kind for t in plan]}
- **Models:** {[t.model for t in plan]}

</details>

```

### 3. `_stream_aggregate(model, messages, compact_results)`
- Строит системный промпт:
  ```
  Ты — финальный агент MWS GPT Auto. Ниже — результаты работы вспомогательных субагентов.
  Используй их как факты. Отвечай на языке пользователя в markdown.
  Если в результатах есть artifacts (изображения), упомяни их через markdown image syntax.

  ---
  [sa_vision] <summary>

  [sa_web_fetch] <summary>
  Citations: [...]
  ...
  ```
- Формирует `messages = [{"role":"system", "content": <scratchpad>}, {"role":"user", "content": <original_last_user>}]`.
- Делает `POST /chat/completions` с `stream=True`.
- Парсит SSE (`data: {...}\n\n`), из каждого chunk извлекает `choices[0].delta.content`, yield'ит.
- На `data: [DONE]` — break.
- После стрима yield'ит ссылки на artifacts (если есть) как markdown-картинки.

### 4. `_maybe_reclassify_stt(results, detected, messages)`
Если в `results` есть CompactResult с `kind="stt"` и НЕТ ещё chat-subagent результата:
- Взять `summary` (транскрипт), положить как новый user-text.
- Запустить `_classify_and_plan` по новому тексту.
- Запустить недостающие субагенты через `_dispatch`.
- Append их results к оригинальным.

### 5. Обработка артефактов
Если `compact_result.artifacts` содержат изображения — aggregator получает инструкцию упомянуть их через markdown `![](<url>)`.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. Запрос "hello" в "MWS GPT Auto" → пользователь видит сначала `<details>` блок, потом стримящийся ответ модели.
2. Загрузка изображения "что это?" → `<details>` показывает `subagents: ['vision']`, потом ответ + ссылка на картинку (если нужно).
3. Стрим работает: токены появляются по одному, не все сразу.
4. Финальная модель видит в system prompt только compact summary, не полные цепочки рассуждений (проверить через debug-лог).
5. Голосовой запрос (stt → реклассификация → chat) работает: `_maybe_reclassify_stt` корректно перезапускает планировщик.

## Dependencies
- phase-9-5, 9-6, 9-7, 9-8 (все субагенты должны быть реализованы).
- phase-9-11 (stub-субагенты тоже должны существовать).

## Out of scope
- Автоматический деплой — phase-9-10.
- Полная E2E верификация — phase-9-12.
