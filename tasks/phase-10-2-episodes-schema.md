# Task: phase-10-2 — ConversationEpisode Schema + Migration

## Goal
Добавить в Memory Service таблицу `conversation_episodes`, которая хранит «оглавление» диалога: по одной строке на окно последних сообщений с summary, эмбеддингом, временем и ссылкой на исходный чат OpenWebUI.

## Context
Сырые сообщения уже лежат в `openwebui.chat` (JSON-блоб), дублировать их не нужно. Нам достаточно индекса: «кто / когда / о чём / ссылка». См. раздел «Архитектура» в `PLAN_db_memory.md`.

## Scope
- В `memory-service/app/models.py` добавить модель:
  ```python
  class ConversationEpisode(Base):
      __tablename__ = "conversation_episodes"
      id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
      user_id = Column(String, index=True, nullable=False)
      chat_id = Column(String, index=True, nullable=False)
      turn_start_at = Column(DateTime(timezone=True), nullable=False)
      turn_end_at = Column(DateTime(timezone=True), nullable=False, index=True)
      summary = Column(Text, nullable=False)
      message_indices = Column(JSON, nullable=False)  # [start, end]
      embedding = Column(Vector(1024), nullable=False)
      created_at = Column(DateTime(timezone=True), server_default=func.now())
      __table_args__ = (
          Index("ix_episodes_user_time", "user_id", "turn_end_at"),
      )
  ```
- Создание таблицы: если проект использует `Base.metadata.create_all` на старте (см. `app/main.py`), модель подхватится автоматически. Иначе — написать Alembic-миграцию.
- После `create_all` в `main.py` выполнить сырым SQL создание ivfflat-индекса (идемпотентно):
  ```sql
  CREATE INDEX IF NOT EXISTS ix_episodes_embedding
    ON conversation_episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
  ```
- Dimension 1024 должна совпадать с `EMBEDDING_DIMENSIONS` из `config.py` и из `docker-compose.yml:137`.

## Files
- `memory-service/app/models.py` (изменить)
- `memory-service/app/main.py` (изменить — ivfflat init hook)

## Acceptance criteria
1. `docker compose build memory-service && docker compose up -d memory-service` поднимается без ошибок.
2. `docker compose exec postgres psql -U mws -d memory -c "\d conversation_episodes"` показывает все колонки с правильными типами, в т.ч. `embedding vector(1024)`.
3. `\di ix_episodes_*` показывает оба индекса: `ix_episodes_user_time` и `ix_episodes_embedding` (ivfflat).
4. Повторный restart memory-service **не** дублирует индекс и не падает.
5. Существующая таблица `memories` не затронута (sanity: `select count(*) from memories;` возвращает прежнее число).

## Dependencies
- phase-10-1 (эмбеддинги должны работать, иначе тесты в следующих фазах бесполезны).

## Out of scope
- Endpoint-ы записи/чтения — phase-10-3 и phase-10-4.
- TTL / очистка старых эпизодов — v2.
