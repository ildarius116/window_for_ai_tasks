# Phase 3.2 — pgvector: Семантический поиск воспоминаний

**Агент:** MemoryAgent + DatabaseAgent
**Зависимости:** 3.1
**Статус:** TODO

## Задача

Реализовать реальный семантический поиск через pgvector и embedding модель.

## Embedding модель

Использовать через LiteLLM:
- `text-embedding-3-small` (OpenAI) если есть ключ
- Или локально через Ollama: `nomic-embed-text`
- Размерность: 1536 (OpenAI) или 768 (nomic)

## Реализация поиска

```python
async def search_memories(user_id: str, query: str, limit: int = 5):
    query_embedding = await get_embedding(query)
    # cosine similarity через pgvector оператор <=>
    results = await db.execute(
        select(Memory)
        .where(Memory.user_id == user_id)
        .order_by(Memory.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    return results.scalars().all()
```

## pgvector настройка

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE INDEX ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
```

## Критерии готовности

- [ ] `POST /memories/search` возвращает топ-5 релевантных воспоминаний
- [ ] Поиск работает по cosine similarity
- [ ] Индекс ivfflat создан (производительность)
- [ ] Embedding реально вычисляется (не заглушка)
