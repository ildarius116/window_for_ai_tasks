# Phase 3.4 — OpenWebUI Pipeline: инжект памяти в контекст

**Агент:** OpenWebUIAgent
**Зависимости:** 3.3
**Статус:** TODO

## Задача

OpenWebUI Function/Pipeline, который при каждом запросе:
1. Получает user_id текущего пользователя
2. Делает семантический поиск по Memory Service (`POST /memories/search`)
3. Инжектирует релевантные воспоминания в системный промпт

## OpenWebUI Pipeline (Python)

```python
class MemoryPipeline:
    def __init__(self):
        self.memory_service_url = os.getenv("MEMORY_SERVICE_URL", "http://memory-service:8000")

    async def inlet(self, body: dict, user: dict) -> dict:
        query = body["messages"][-1]["content"]
        user_id = user.get("id", "")

        memories = await search_memories(user_id, query)

        if memories:
            memory_context = "\n".join(f"- {m}" for m in memories)
            system_inject = f"Что ты знаешь о пользователе:\n{memory_context}"
            # Добавить в system message
            body = inject_system_message(body, system_inject)

        return body
```

## Размещение

OpenWebUI поддерживает Pipelines через `PIPELINES_URLS` env var или через admin UI.
Создать `pipelines/memory_pipeline.py`.

## Критерии готовности

- [ ] Pipeline активирован в OpenWebUI
- [ ] При отправке сообщения — релевантная память попадает в контекст
- [ ] Можно проверить через OpenWebUI debug mode (показывает system prompt)
