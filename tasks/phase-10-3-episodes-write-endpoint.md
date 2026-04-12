# Task: phase-10-3 — POST /episodes (Write Endpoint + Summary Generation)

## Goal
Добавить в Memory Service эндпоинт, который принимает окно сообщений от OpenWebUI filter-а, генерирует 1-2-предложный summary через LiteLLM, считает эмбеддинг и INSERT-ит строку в `conversation_episodes`.

## Context
Этот эндпоинт будет дёргаться из `pipelines/memory_function.py` в `outlet()` (phase-10-5). Summary должен быть коротким и фактическим — цель в том, чтобы recall находил эпизод по теме, а не по точным словам.

## Scope
- Новый файл `memory-service/app/episodes.py` с функцией:
  ```python
  async def generate_summary(messages: list[dict]) -> str
  ```
  - Вызывает LiteLLM через существующий helper (если есть) или напрямую `httpx.AsyncClient`.
  - Модель: `config.SUMMARY_MODEL` (default `mws/gpt-alpha`). Новая переменная в `config.py`.
  - System prompt (RU+EN): «Summarize the conversation in 1-2 sentences. Plain text, no preamble, no bullets. Focus on topic and user intent.»
  - `temperature=0.1`, `max_tokens=120`.
  - На ошибку — raise (пусть эндпоинт отдаст 502).

- Новый файл `memory-service/app/routers/episodes.py`:
  - `POST /episodes`, body (Pydantic `EpisodeCreate`):
    ```python
    class EpisodeCreate(BaseModel):
        user_id: str
        chat_id: str
        messages: list[dict]            # [{"role": "...", "content": "..."}, ...]
        message_indices: list[int]      # [start, end]
        turn_start_at: datetime
        turn_end_at: datetime
    ```
  - Flow:
    1. `summary = await generate_summary(messages)`
    2. `vec = await get_embedding(summary)` — эмбеддим именно summary, не весь диалог.
    3. INSERT в `conversation_episodes`.
    4. Response `EpisodeOut` — сохранённая строка без raw messages.
  - На пустом `messages[]` — 400.

- В `memory-service/app/main.py` зарегистрировать `episodes_router`.
- В `memory-service/app/config.py` добавить `SUMMARY_MODEL: str = os.getenv("SUMMARY_MODEL", "mws/gpt-alpha")`.
- В `docker-compose.yml` у `memory-service` добавить `SUMMARY_MODEL: mws/gpt-alpha` в env (для явности).

## Files
- `memory-service/app/episodes.py` (new)
- `memory-service/app/routers/episodes.py` (new)
- `memory-service/app/main.py` (изменить)
- `memory-service/app/config.py` (изменить)
- `docker-compose.yml` (изменить — env)

## Acceptance criteria
1. `curl` на эндпоинт изнутри сети (`docker compose exec memory-service curl -X POST http://localhost:8000/episodes -H 'content-type: application/json' -d '{"user_id":"u1","chat_id":"c1","messages":[{"role":"user","content":"Как настроить nginx reverse proxy?"},{"role":"assistant","content":"Добавь server block с proxy_pass..."}],"message_indices":[0,1],"turn_start_at":"2026-04-11T10:00:00Z","turn_end_at":"2026-04-11T10:01:00Z"}'`) возвращает 200 с телом, содержащим непустой `summary`.
2. `select summary, turn_end_at from conversation_episodes where user_id='u1';` показывает короткую осмысленную строку (не copy-paste всего диалога).
3. `select vector_dims(embedding) from conversation_episodes where user_id='u1';` → 1024.
4. Пустой `messages` → HTTP 400.
5. Недоступный LiteLLM → HTTP 502 с понятной ошибкой, строка в БД **не** создаётся.

## Dependencies
- phase-10-1 (работающие эмбеддинги).
- phase-10-2 (таблица).

## Out of scope
- Recall/search — phase-10-4.
- Вызов из outlet — phase-10-5.
