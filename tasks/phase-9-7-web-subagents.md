# Task: phase-9-7 — Web Subagents (fetch, search)

## Goal
Реализовать два субагента: `sa_web_fetch` (скачать и суммаризовать URL из сообщения) и `sa_web_search` (поиск в интернете через DuckDuckGo с суммаризацией топ-3 результатов). Включить встроенный web search в OpenWebUI.

## Context
См. `PLAN_chat_agents.md` разделы 7 и 11. Решено использовать бесплатный DuckDuckGo без новых контейнеров. Обязательные фичи xlsx #7 (поиск в интернете) и #8 (веб-парсинг ссылки).

## Scope

### `_sa_web_fetch(task)`
- Из `task.metadata["urls"]` или `task.input_text` достать первую URL.
- Через `httpx.AsyncClient` сделать GET (timeout 10s, `User-Agent: Mozilla/5.0 MWS-GPT-Hub`).
- Извлечь текст: попытаться через `selectolax.parser.HTMLParser` (или `BeautifulSoup` если доступен) — взять `<article>`, `<main>` или `body`, вырезать `<script>`, `<style>`, `<nav>`, `<footer>`.
- Текст обрезать до ~6000 символов.
- Вызвать `mws/llama-3.1-8b` с system "Ты — суммаризатор веб-страниц. Выдели главное в 3-5 предложениях на языке пользователя.", user = извлечённый текст.
- Summary = ответ модели + строка `Источник: <url>`.
- `citations = [url]`.

### `_sa_web_search(task)`
- Использовать `duckduckgo_search` (pip `ddgs` или `duckduckgo-search`). Если пакет недоступен в OpenWebUI контейнере, делать прямой запрос к DuckDuckGo HTML endpoint через httpx.
- Альтернатива: вызвать OpenWebUI внутренний endpoint `/api/v1/retrieval/process/web/search` (он настроен через env).
- Получить 3 топ-результата (title, url, snippet).
- Fetch каждую URL (параллельно через `asyncio.gather`), извлечь текст, обрезать до 2000 символов.
- Вызвать `mws/kimi-k2` с system "Ты — поисковый агент. Ответь на вопрос пользователя опираясь на найденные фрагменты. Цитируй источники как [1], [2], [3].", user = `<question>\n\n[1] <snippet1>\n[2] <snippet2>\n[3] <snippet3>`.
- Summary = ответ модели.
- `citations = [url1, url2, url3]`.

### Env changes
Добавить в `docker-compose.yml` в секцию `openwebui` → `environment`:
```
ENABLE_RAG_WEB_SEARCH: "true"
RAG_WEB_SEARCH_ENGINE: "duckduckgo"
RAG_WEB_SEARCH_RESULT_COUNT: "3"
```

## Files
- `pipelines/auto_router_function.py` (изменить)
- `docker-compose.yml` (изменить — env vars)

## Acceptance criteria
1. Сообщение "https://example.com" → `sa_web_fetch` возвращает summary с reference на example.com.
2. Сообщение "Что нового в Qwen 3?" → `sa_web_search` возвращает summary со ссылками `[1]`, `[2]`, `[3]`.
3. При недоступности URL (404, timeout) субагент возвращает `CompactResult(error=...)`, не падает.
4. `docker compose config` валидирует env-переменные без ошибок.
5. После `docker compose up -d --force-recreate openwebui` встроенный web search в OpenWebUI UI тоже работает (toggle в чате).

## Dependencies
- phase-9-4 (интерфейс).

## Out of scope
- Rate limiting поиска.
- Caching результатов fetch.
