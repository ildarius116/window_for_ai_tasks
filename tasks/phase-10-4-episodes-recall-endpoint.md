# Task: phase-10-4 — POST /episodes/recall (Semantic + Time-Window Search)

## Goal
Добавить в Memory Service эндпоинт поиска эпизодов по user_id, текстовому запросу и опциональному временному окну. Это то, что дёргает subagent `sa_memory_recall` (phase-10-8).

## Context
Запросы бывают двух типов:
- **Чисто семантические**: «о чём мы обсуждали nginx» → cosine-поиск без фильтра по дате.
- **С временным маркером**: «о чём мы говорили 3 месяца назад», «last week» → классификатор выдаёт `time_window: {from, to}`, мы сужаем выборку через `WHERE turn_end_at BETWEEN ?`.

## Scope
- В `memory-service/app/routers/episodes.py` добавить:
  - `POST /episodes/recall`, body:
    ```python
    class EpisodeRecall(BaseModel):
        user_id: str
        query: str
        date_from: Optional[datetime] = None
        date_to:   Optional[datetime] = None
        limit: int = 5
    ```
  - Flow:
    1. `qvec = await get_embedding(query)`.
    2. SQL (через SQLAlchemy или сырой):
       ```sql
       SELECT id, chat_id, turn_start_at, turn_end_at, summary, message_indices,
              1 - (embedding <=> :qvec) AS score
       FROM conversation_episodes
       WHERE user_id = :user_id
         AND (:date_from IS NULL OR turn_end_at >= :date_from)
         AND (:date_to   IS NULL OR turn_end_at <= :date_to)
       ORDER BY embedding <=> :qvec
       LIMIT :limit
       ```
    3. Response — список `EpisodeRecallResult` со `score`.
  - Лимит жёстко ограничить сверху (например `min(limit, 20)`).

- Пустой результат — 200 с `[]`, не 404.

## Files
- `memory-service/app/routers/episodes.py` (изменить)

## Acceptance criteria
1. После phase-10-3 записать 3 эпизода про разные темы (nginx, python async, рецепт борща) на одного user_id. Recall `{"user_id":"u1","query":"сетевой прокси"}` возвращает nginx-эпизод на первом месте (`score` самый высокий).
2. Recall того же запроса, но с `user_id="u2"` — возвращает `[]`.
3. Recall с `date_from="2026-04-10"` и `date_to="2026-04-11"` возвращает только эпизоды в этом окне.
4. После UPDATE `turn_end_at = now() - interval '95 days'` у одной строки: recall с `date_from=now()-120d`, `date_to=now()-60d` возвращает именно её.
5. `limit=3` вернёт ≤3 строк, `limit=999` отработает как `limit=20`.

## Dependencies
- phase-10-2, phase-10-3.

## Out of scope
- Parsing временных выражений из query — это делает классификатор в router-е, а не memory-service.
- Reranking, BM25, hybrid — всё сверху cosine.
