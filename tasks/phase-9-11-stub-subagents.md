# Task: phase-9-11 — Stub Subagents (deep_research, presentation)

## Goal
Зарегистрировать субагенты для дополнительных фич xlsx (#13 Deep Research, #14 Генерация презентаций) как stub'ы, которые возвращают информативное сообщение "⚠️ Будет добавлено в v2". Classifier уже умеет их распознавать.

## Context
Решение пользователя: в v1 только stub'ы для доп. фич, полная реализация — v2. Это сохраняет совместимость classifier (не меняется JSON-схема) и даёт пользователю понятное сообщение, а не падение. См. `PLAN_chat_agents.md` раздел 7.

## Scope

### `_sa_deep_research(task)`
```python
async def _sa_deep_research(self, task: SubTask) -> CompactResult:
    return CompactResult(
        kind="deep_research",
        summary=(
            "⚠️ **Deep Research** будет добавлен в v2.\n\n"
            "В следующей версии я смогу провести многошаговое исследование: "
            "собрать факты из нескольких источников, проверить их и сделать вывод. "
            "Пока же могу ответить на основе одного поискового запроса через `mws/kimi-k2`."
        ),
    )
```

### `_sa_presentation(task)`
```python
async def _sa_presentation(self, task: SubTask) -> CompactResult:
    return CompactResult(
        kind="presentation",
        summary=(
            "⚠️ **Генерация презентаций** будет добавлена в v2.\n\n"
            "В v2 я смогу сгенерировать Marp/Reveal.js markdown с картинками. "
            "Пока могу создать структуру презентации в markdown — просто попросите."
        ),
    )
```

### Classifier integration
Проверить, что `mws/gpt-oss-20b` действительно может вернуть `intents: ["deep_research"]` или `["presentation"]`. Если модель их не знает — дописать в system prompt classifier'а примеры:
```
Examples:
- "Провести глубокое исследование рынка EV" -> {"intents":["deep_research"],...}
- "Сделай презентацию про Python" -> {"intents":["presentation"],...}
```

### UX: finalize'у передать stub summary
Финальная модель в `_stream_aggregate` увидит stub-сообщение и может его пересказать пользователю или дополнить собственным ответом. Проверить, что финальный вывод корректный.

## Files
- `pipelines/auto_router_function.py` (изменить — заменить stub'ы из phase-9-4 на информативные сообщения).

## Acceptance criteria
1. Запрос "Проведи deep research по рынку электромобилей" → routing decision показывает `subagents: ['deep_research']`, финальный ответ содержит строку "Deep Research будет добавлен в v2".
2. Запрос "Сделай презентацию на 10 слайдов про Rust" → routing содержит `presentation`, финальный ответ пользователя уведомляет о stub'е.
3. Classifier стабильно возвращает эти intents для соответствующих запросов (проверить вручную 3-4 примера).
4. Pipe не падает, пользователь получает читаемый ответ.

## Dependencies
- phase-9-3 (classifier должен уметь различать intents).
- phase-9-4 (интерфейс).

## Out of scope
- Реальная реализация Deep Research/Presentation — это v2, отдельная фаза.
