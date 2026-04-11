# Task: phase-10-1 — Fix Embedding Fallback in Memory Service

## Goal
Убрать мёртвый OpenAI-фолбэк `text-embedding-3-small` в `memory-service/app/embedding.py`, убедиться что основной путь через LiteLLM → `mws/bge-m3` отдаёт настоящие 1024-dim векторы, а не хеш-псевдоэмбеддинги. Без этой починки семантический recall эпизодов (phase-10-4) бесполезен.

## Context
Текущий код в `memory-service/app/embedding.py` при ошибке LiteLLM дропает к хардкоду `text-embedding-3-small`, которого нет в `litellm/config.yaml`, из-за чего вызов снова падает и уходит в детерминированный хеш (`_hash_embedding`). Хеш-вектор даёт одинаковые результаты независимо от смысла, что ломает cosine-поиск. См. `memory-service/app/embedding.py:59-62` и комментарий «not semantically meaningful» на line 69.

`EMBEDDING_MODEL=mws/bge-m3` уже выставлен в `docker-compose.yml:136` — основной путь должен работать.

## Scope
- Удалить секцию фолбэка на `text-embedding-3-small` в `embedding.py`.
- Оставить **только** вызов через LiteLLM с моделью из `config.py` (`EMBEDDING_MODEL`).
- На ошибку (httpx/timeout/4xx/5xx) — `raise` наверх, не скрывать. Пусть `/episodes` и `/memories/extract` падают с явной ошибкой вместо тихого зачисления мусора в pgvector.
- Хеш-фолбэк `_hash_embedding` удалить целиком (не нужен в проде) либо оставить **только** за явным флагом `ALLOW_HASH_EMBEDDING_FALLBACK=true` для локальной разработки.
- Добавить лог на уровне `INFO` при старте: «Embedding model: mws/bge-m3, dim=1024».

## Files
- `memory-service/app/embedding.py` (изменить)
- `memory-service/app/config.py` (опционально — флаг)

## Acceptance criteria
1. `docker compose exec memory-service python -c "import asyncio; from app.embedding import get_embedding; v=asyncio.run(get_embedding('привет')); print(len(v), v[:3])"` печатает `1024` и три ненулевых float — **не** 0.0/детерминированные хеш-значения.
2. Два разных текста дают разные векторы с cosine-similarity < 0.99 (быстрый sanity-check на то, что это не хеш).
3. При отключённом LiteLLM (`docker compose stop litellm`) вызов `get_embedding` **падает** с явной ошибкой, а не возвращает хеш.
4. `docker compose logs memory-service | grep Embedding` показывает строку с моделью и размерностью.

## Dependencies
- Нет (эта задача — прерэквизит ко всем остальным phase-10 задачам).

## Out of scope
- Добавление новых эмбеддинг-моделей или реранкеров.
- Миграция старых facts-записей с хеш-векторами (их можно удалить вручную позже).
