# phase-12-10 — Документация и `tasks_done/phase-12-done.md`

## Цель
Зафиксировать результат phase-12 в документации и отчёте, чтобы будущие задачи и новые контрибьютеры сразу видели, что делает фактчекер, как его выключить и где искать отчёты.

## Что сделать
- Обновить `CLAUDE.md`:
  - В секции «Development Conventions» добавить абзац про fact-check:
    «`_sa_fact_check` (phase-12) — двухфазный: запускается ПОСЛЕ основной `asyncio.gather`, валидирует URL (httpx HEAD/GET + SSRF-блок), извлекает claims через `mws/gpt-oss-20b` (JSON), выставляет verdict через `mws/kimi-k2`. Активируется, если в плане есть `_CHECKABLE_KINDS` (web_search/web_fetch/deep_research/memory_recall/doc_qa) или сработал `_FACT_CHECK_TRIGGER_RE`. Аггрегатор получает метки ✅/⚠️/❌ в system-prompt и отдаёт свёрнутый details-блок "Проверка источников" в конце ответа. Выключается через `valves.fact_check_enabled=False`.»
  - В секции «Project Status» добавить блок:
    «**Phase 12 — Fact-Checker Subagent (done, {дата}):** Отчёт в `tasks_done/phase-12-done.md`. Дизайн в `PLAN_truth_agent.md`. Основные решения: фактчекер — не часть плана LLM-классификатора (автоактивация по типу субагентов), двухфазная оркестрация, URL-валидация через httpx + SSRF-блок, LLM-вердикт только с `evidence_url ∈ url_ok_set` (защита от галлюцинации proven).»
  - В списке `Key Files` добавить:
    - `PLAN_truth_agent.md` — дизайн-док fact-checker'а.
    - `scripts/e2e_fact_check_test.py` — smoke-тесты F1/F3/F4/F5.
- Обновить `.env.example`:
  - Комментарий, что отдельных env vars фактчекер не требует (всё через OpenWebUI valves), но `MWS_GPT_API_KEY` остаётся необходим — `gpt-oss-20b` и `kimi-k2` ходят через LiteLLM.
- Создать `tasks_done/phase-12-done.md` по образцу `tasks_done/phase-11-done.md`:
  - Раздел «Что сделано» — перечень всех 10 задач с короткими итогами.
  - Раздел «Архитектурные решения» — почему не через LLM-классификатор, почему после gather, почему httpx-HEAD.
  - Раздел «Incidents caught during E2E» — если во время phase-12-9 нашлись баги, записать их сюда с описанием фикса (как сделано в phase-11-done.md для `max_tokens=2000`, `X-Title-B64` и т. п.).
  - Раздел «Limits / not in scope» — явно перечислить: нет Redis-кеша верификаций, нет поиска противоречий между источниками, нет проверки image_gen.
- Переместить `tasks/phase-12-*.md` в `tasks_done/phase-12-*.md` только ПОСЛЕ закрытия всех 10 пунктов.

## Критерии готовности
- `CLAUDE.md` читается без противоречий, не разрастается и сохраняет структуру.
- `tasks_done/phase-12-done.md` содержит все 10 подзадач с итогами и хотя бы один пункт в «Incidents».
- `tasks/phase-12-*.md` больше нет (перенесены в `tasks_done/`).
- `git status` чистый, одним коммитом (или серией осмысленных коммитов) phase-12 закрыта.

## Затронутые файлы
- `CLAUDE.md`
- `.env.example`
- `tasks_done/phase-12-done.md` (new)
- `tasks/phase-12-*.md` → `tasks_done/phase-12-*.md`
