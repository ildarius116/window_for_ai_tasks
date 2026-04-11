# Task: phase-9-8 — Doc Q&A Subagent

## Goal
Реализовать субагент `sa_doc_qa`, который отвечает на вопросы по прикреплённым файлам (PDF/DOCX/TXT/MD), используя встроенный RAG OpenWebUI — не дублируя векторизацию.

## Context
OpenWebUI уже настроен с `RAG_EMBEDDING_MODEL=mws/bge-m3` и `RAG_TOP_K=5` (см. `docker-compose.yml`). Файлы, загруженные в чат, автоматически индексируются. Нам не нужно вызывать bge-m3 напрямую — достаточно получить готовый RAG-контекст. См. `PLAN_chat_agents.md` раздел 7.

## Scope
- Исследовать, как OpenWebUI передаёт файлы/контекст в Pipe-функцию:
  - Опция А: `body["files"]` содержит уже извлечённый текст (проверить на реальном upload в phase-9-1 scaffold).
  - Опция Б: OpenWebUI вызывает `/api/v1/retrieval/process/file` и добавляет результат в `messages[0]["content"]` как context-блок.
  - Опция В: нужно из Pipe вызвать `/api/v1/knowledge/{id}/query` или `/api/v1/retrieval/query` с вопросом пользователя.
- Задокументировать в комментариях внутри метода `_sa_doc_qa`, какой вариант выбран.
- Реализация:
  - Собрать относящийся к документу контекст (из body или через RAG API).
  - Если контекст > 100K символов → не помещать целиком, а сделать выборку top-k (использовать существующий OpenWebUI retrieval endpoint).
  - Вызвать `mws/glm-4.6` (200K context window) с system "Отвечай на вопрос по предоставленному документу. Цитируй разделы/страницы. Если ответа нет в документе — скажи прямо." + user = `<question>\n\n--- DOCUMENT ---\n<context>`.
  - Summary = ответ модели.
  - `citations = [file_name, ...]`.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. Загрузить PDF в чат OpenWebUI, спросить "summarize chapter 2" → `sa_doc_qa` возвращает summary с ссылкой на главу и имя файла в `citations`.
2. При отсутствии документа — субагент возвращает `CompactResult(error="no document context")`.
3. При документе > 100K символов не отправляем его весь в `glm-4.6`, а выбираем top-k через OpenWebUI retrieval API.
4. Комментарий в коде объясняет, какой из вариантов (А/Б/В) используется и почему.

## Dependencies
- phase-9-4 (интерфейс).

## Out of scope
- Собственная re-индексация файлов.
- Fallback на другую LLM — всегда glm-4.6 для long context.
