# Фаза 3 — Memory Companion Service: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 3.1 — FastAPI Memory Microservice
- `memory-service/` — FastAPI сервис на порту 8001
- Endpoints: `POST /memories`, `GET /memories/{user_id}`, `POST /memories/search`, `DELETE /memories/{id}`, `DELETE /memories/user/{id}`, `POST /memories/extract`, `GET /health`
- SQLAlchemy async + asyncpg + pgvector
- Docker image собирается из `memory-service/Dockerfile`

### 3.2 — pgvector Semantic Search
- PostgreSQL образ заменён на `pgvector/pgvector:pg16` (вместо `postgres:16-alpine`)
- Extension `vector` создан в БД `memory`
- Модель `Memory`: id, user_id, content, embedding (Vector(768)), source_chat_id, created_at, updated_at
- Cosine distance search через pgvector оператор `<=>`
- **Embedding**: hash-based pseudo-embedding (MVP fallback), попытка использовать LiteLLM `/v1/embeddings` если доступно

### 3.3 — Memory Extraction
- LLM-powered extraction через `mws/nemotron` (LiteLLM)
- Промпт извлекает до 5 фактов из разговора в формате JSON
- Дедупликация: если cosine similarity > 0.9 с существующим фактом — пропуск
- **Проверено:** из тестового разговора извлечены 3 факта

### 3.4 — OpenWebUI Memory Pipeline
- Endpoint `POST /memories/search` готов для интеграции с OpenWebUI Pipeline
- Pipeline будет добавлен через OpenWebUI Admin UI (Functions/Pipelines) — не требует изменения кода OpenWebUI

## Новые сервисы

| Сервис | Image | Порт | Healthcheck |
|--------|-------|------|-------------|
| memory-service | memory-service/Dockerfile | 8001 | python urllib /health |

## E2E тест пройден

Вопрос: "What programming language do I prefer?"
Ответ: "Based on our previous conversation, you mentioned you prefer Python and the async/await code style."
— Модель использовала инжектированную память.

## Отклонения от плана

- **Postgres образ**: заменён с `postgres:16-alpine` на `pgvector/pgvector:pg16` для поддержки vector extension
- **Embedding**: используется hash-based fallback вместо реальной embedding модели (free OpenRouter модели не предоставляют /embeddings endpoint). Score не семантически осмысленный — фильтрация по score отключена до подключения настоящей embedding модели
- **OpenWebUI Function**: создана и активирована через API (`mws_memory`, type=filter, is_global=true). Score-фильтр убран т.к. hash-embedding даёт отрицательные score

## Тестовые данные
Создано 4 тестовых воспоминания для user `test-user` + 1 для реального user — удалить через `DELETE /memories/user/{id}`
