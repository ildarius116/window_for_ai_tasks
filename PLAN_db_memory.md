# PLAN — Persistent Conversation Memory & Time-Based Recall

## Context

Сейчас в стеке две формы памяти:

1. **OpenWebUI таблица `chat`** (БД `openwebui`, теперь bind-mount `./data/postgres`) — хранит **всю** переписку, но сообщения лежат одним JSON-блобом в колонке `chat`. SQL-фильтр «3 месяца назад» по содержимому неудобен.
2. **Memory Service** (`memory-service/`, БД `memory`, таблица `memories`) — хранит **только извлечённые факты** (1024-dim pgvector, `mws/bge-m3`). Сырые Q/A после экстракции выбрасываются. См. `memory-service/app/models.py:15-36`.

Чего не хватает: чат не может ответить «о чём мы говорили 3 месяца назад», потому что (а) сырая история есть только в JSON-блобе, (б) у фактов нет привязки к времени диалога и нет «оглавления». Auto-router (`pipelines/auto_router_function.py`) тоже не получает `user_id` от OpenWebUI — даже если бы был subagent для recall, он не знал бы, чьи воспоминания искать.

**Цель.** Добавить слой **conversation episodes** (хронологическое «оглавление» с семантическим поиском по времени и содержанию) и subagent `memory_recall`, который этот слой использует. Текущая экстракция фактов (durable личные предпочтения) **остаётся параллельно** — она закрывает «помни, кто я и какие у меня привычки», эпизоды закрывают «о чём мы говорили в такую-то дату».

Сырые Q/A **не дублируем**: они уже лежат в OpenWebUI `chat` таблице, теперь персистентной благодаря bind-mount. В эпизоде храним только summary + ссылку (`chat_id` + диапазон индексов сообщений) — если когда-нибудь понадобится подтянуть исходник, это делается одним JOIN-ом по тому же postgres.

## Архитектура

```
User → OpenWebUI chat
            │
            ├─→ chat table (JSON blob, full text)             ← OpenWebUI persistence
            │
            ├─→ memory_function.outlet (every 4 user turns)
            │       ├─→ POST /memories/extract  (existing — facts)
            │       └─→ POST /episodes          (NEW — summary + embedding)
            │
            └─→ auto_router pipe
                    ├─ classifier: detects "memory_recall" intent + time_window
                    └─→ sa_memory_recall
                            └─→ POST /episodes/recall  (semantic + time window)
                                    └─ optional deep-fetch from openwebui.chat by chat_id
```

## Изменения по файлам

### 1. Memory Service — новая таблица и эндпоинты

**`memory-service/app/models.py`** — добавить модель:

```python
class ConversationEpisode(Base):
    __tablename__ = "conversation_episodes"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(String, index=True, nullable=False)
    chat_id = Column(String, index=True, nullable=False)        # OpenWebUI chat.id
    turn_start_at = Column(DateTime(timezone=True), nullable=False)
    turn_end_at = Column(DateTime(timezone=True), nullable=False, index=True)
    summary = Column(Text, nullable=False)                       # 1-2 предложения
    message_indices = Column(JSON, nullable=False)               # [start, end] в chat.history.messages
    embedding = Column(Vector(1024), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_episodes_user_time", "user_id", "turn_end_at"),
    )
```

Индексы критичны: `(user_id, turn_end_at DESC)` для time-window запросов + ivfflat на `embedding` (создать в `init`-хуке через сырой SQL).

**`memory-service/app/episodes.py`** (новый файл) — генерация summary через LiteLLM (`mws/gpt-alpha`, JSON-mode, prompt: «1-2 предложения, что обсуждали, без вступлений»). Эмбеддинг через существующий `embedding.get_embedding()`.

**`memory-service/app/routers/episodes.py`** (новый):

- `POST /episodes` — body: `{user_id, chat_id, messages, message_indices, turn_start_at, turn_end_at}`. Считает summary + embedding, INSERT.
- `POST /episodes/recall` — body: `{user_id, query, date_from?, date_to?, limit=5}`. SQL: `WHERE user_id=? [AND turn_end_at BETWEEN ?]` ORDER BY `embedding <=> query_embedding`. Возвращает `[{summary, turn_start_at, turn_end_at, chat_id, message_indices, score}]`.

**`memory-service/app/main.py`** — зарегистрировать новый router.

**Прерэквизит — починить эмбеддинги.** В `memory-service/app/embedding.py:59-62` есть фолбэк на несуществующий `text-embedding-3-small`, который скатывает к hash-псевдоэмбеддингам. Для семантического recall это бесполезно. Перед валидацией убедиться, что `EMBEDDING_MODEL=mws/bge-m3` реально работает (он уже выставлен в `docker-compose.yml:136`), и удалить мёртвый OpenAI-фолбэк.

### 2. OpenWebUI filter — пишем эпизоды на каждом outlet

**`pipelines/memory_function.py`** — в `outlet()` (line 100-126), после существующего `/memories/extract` (который не трогаем), добавить второй POST на `/episodes`. Throttle тот же — каждые 4 пользовательских сообщения. Чтобы избежать перекрытий, для v1 окно последних 8 сообщений; дубли близких summary не критичны (recall возвращает top-K по cosine).

`turn_start_at`/`turn_end_at` берём из `messages[i].timestamp` если они есть в OpenWebUI body, иначе `datetime.now(UTC)` для обоих.

### 3. Auto-router — новый subagent + плумбинг user_id

**`pipelines/auto_router_function.py`**:

- **Плумбинг user_id (критично).** В `pipe()` (line 134-142) сейчас принимается `__user__: Optional[dict]`, но он никуда не уходит. Сохранить `user_id = (__user__ or {}).get("id")` и при создании каждого `SubTask` класть в `metadata["user_id"] = user_id`.
- **Регекс шорт-сёркит.** Рядом с `_REASONER_RE` (line ~83) добавить:
  ```python
  _MEMORY_RECALL_RE = re.compile(
      r"(?i)(о ч[её]м мы говорили|когда мы говорили|что я тебе (рассказывал|говорил)|"
      r"помнишь, как|на прошлой неделе|месяца? назад|год назад|вчера мы|"
      r"what did we (discuss|talk about)|do you remember|last (week|month|year)|ago we)"
  )
  ```
  В `_classify_and_plan` поставить проверку **до** длины-1500 шорт-сёркита, чтобы recall на длинных вопросах не утекал в `long_doc`.
- **Классификатор.** В system-prompt JSON-классификатора (line ~386-399) добавить интент `memory_recall` и попросить выдавать опциональное поле `time_window: {from, to}` в ISO-формате, если в запросе есть временные маркеры. В `kind_map` (line ~424-433) добавить `"memory_recall": "memory_recall"`.
- **Dispatch.** В словарь `dispatch` в `_run_subagent` (line 530-544) добавить `"memory_recall": self._sa_memory_recall`.
- **Subagent.** Новый метод `_sa_memory_recall`: читает `user_id` и `time_window` из `task.metadata`, POST на `http://memory-service:8000/episodes/recall`, форматирует ответ как `- [YYYY-MM-DD] summary` строки, упаковывает в `CompactResult` с `_truncate_tokens(..., 500)`.

## Критичные файлы

| Файл | Что |
|---|---|
| `memory-service/app/models.py` | + `ConversationEpisode` |
| `memory-service/app/episodes.py` (new) | summary generation |
| `memory-service/app/routers/episodes.py` (new) | `/episodes`, `/episodes/recall` |
| `memory-service/app/main.py` | register router, ivfflat init |
| `memory-service/app/embedding.py:59-62` | удалить мёртвый openai-фолбэк |
| `pipelines/memory_function.py:100-126` | + POST `/episodes` в outlet |
| `pipelines/auto_router_function.py:83` | `_MEMORY_RECALL_RE` |
| `pipelines/auto_router_function.py:134-142` | прокинуть `user_id` в SubTask.metadata |
| `pipelines/auto_router_function.py:386-433` | классификатор: интент `memory_recall` + `time_window` |
| `pipelines/auto_router_function.py:530-544` | dispatch + `_sa_memory_recall` |

Деплой пайплайнов: bootstrap-сайдкар уже умеет UPSERT-ить `auto_router_function.py` и `memory_function.py` в таблицу `function` — `docker compose restart bootstrap` после правок подхватит обе версии. Memory Service пересобирается через `docker compose build memory-service && docker compose up -d memory-service`.

## Verification

1. **Эмбеддинги работают.**
   `docker compose exec memory-service python -c "import asyncio; from app.embedding import get_embedding; print(len(asyncio.run(get_embedding('hello'))))"` — должно быть 1024 и **не** хеш-фолбэк (логи без warning-ов).
2. **Запись эпизодов.** Поговорить с ботом 5 сообщений → `docker compose exec postgres psql -U mws -d memory -c "select user_id, chat_id, turn_end_at, summary from conversation_episodes order by created_at desc limit 5;"` — должна быть свежая строка.
3. **Recall — семантический.** Сделать 3 разных диалога в разные минуты, потом в новом чате спросить «о чём мы говорили про <тему первого диалога>» → ответ должен прийти через `sa_memory_recall` (виден в логах router-а как `kind=memory_recall`) и содержать дату/summary первого диалога.
4. **Recall — временной.** В postgres вручную сдвинуть `turn_end_at` одного эпизода на `now() - interval '95 days'`, спросить «о чём мы говорили 3 месяца назад» → классификатор должен выдать `time_window`, recall — вернуть именно этот эпизод.
5. **User scoping.** Создать второго пользователя в OpenWebUI, проверить что recall первого юзера не видит эпизоды второго (`user_id` в SubTask.metadata).
6. **Персистентность.** `docker compose down && docker compose up -d` → эпизоды на месте (БД bind-mounted).
7. **Существующая экстракция фактов не сломана.** `select count(*) from memories;` после нового диалога → счётчик должен расти как раньше; inlet продолжает инжектить факты в system prompt.

## Phase-10 task breakdown

| # | Файл | Задача |
|---|---|---|
| 10-1 | `tasks/phase-10-1-fix-embedding-fallback.md` | Удалить мёртвый openai-фолбэк, проверить bge-m3 |
| 10-2 | `tasks/phase-10-2-episodes-schema.md` | Модель `ConversationEpisode` + миграция/ivfflat |
| 10-3 | `tasks/phase-10-3-episodes-write-endpoint.md` | `POST /episodes` + summary generation |
| 10-4 | `tasks/phase-10-4-episodes-recall-endpoint.md` | `POST /episodes/recall` (semantic + time window) |
| 10-5 | `tasks/phase-10-5-memory-function-outlet.md` | Дописать outlet → POST /episodes |
| 10-6 | `tasks/phase-10-6-router-user-id-plumbing.md` | Прокинуть `user_id` в SubTask.metadata |
| 10-7 | `tasks/phase-10-7-router-classifier-memory-recall.md` | Регекс + интент классификатора + `time_window` |
| 10-8 | `tasks/phase-10-8-sa-memory-recall.md` | Сам субагент + dispatch entry |
| 10-9 | `tasks/phase-10-9-e2e-verification.md` | Прогон сценариев из Verification |

## Открытые вопросы (для v2, не блокеры)

- **Deep-fetch исходных сообщений.** Если пользователь скажет «процитируй точно, что я писал» — для v1 subagent отвечает только summary. В v2 можно добавить `GET /episodes/{id}/messages`, который читает `openwebui.chat` по `chat_id` + `message_indices` (отдельный async engine на ту же postgres-инстанцию).
- **Дедупликация перекрывающихся окон.** Сейчас каждый outlet пишет окно последних 8 сообщений; если outlet триггерится каждые 4 — будет ~50% перекрытия. Для v1 терпимо. В v2 — хранить `last_indexed_message_idx` per chat.
- **Очистка старого/PII.** Сейчас эпизоды живут вечно. Когда понадобится — добавить TTL-job или DELETE endpoint по user_id + дате.
