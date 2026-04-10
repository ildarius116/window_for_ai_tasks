# Phase 3.1 — Memory FastAPI Microservice

**Агент:** BackendCoderAgent + MemoryAgent
**Зависимости:** 1.3
**Статус:** TODO

## Задача

Создать отдельный FastAPI сервис для хранения долгосрочной памяти пользователей.

## Структура

```
memory-service/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py       # SQLAlchemy: Memory
│   ├── schemas.py      # Pydantic
│   ├── database.py
│   └── routers/
│       └── memories.py
├── Dockerfile
└── requirements.txt
```

## API Endpoints

```
POST   /memories              — сохранить воспоминание
GET    /memories/{user_id}    — список воспоминаний пользователя
POST   /memories/search       — семантический поиск (query + user_id)
DELETE /memories/{id}         — удалить конкретное воспоминание
DELETE /memories/user/{id}    — удалить все воспоминания пользователя
GET    /health                — healthcheck
```

## Модель Memory (PostgreSQL + pgvector)

```python
class Memory(Base):
    id: UUID
    user_id: str           # совпадает с user_id в OpenWebUI
    content: str           # текст воспоминания
    embedding: Vector(1536) # pgvector
    source_chat_id: str    # откуда извлечено
    created_at: datetime
    updated_at: datetime
```

## docker-compose добавить

```yaml
memory-service:
  build: ./memory-service
  ports: ["8001:8000"]
  environment:
    DATABASE_URL: postgresql://...pgvector БД
```

## Критерии готовности

- [ ] `GET /health` возвращает 200
- [ ] `POST /memories` сохраняет запись
- [ ] `GET /memories/{user_id}` возвращает список
- [ ] Сервис в docker-compose поднимается
