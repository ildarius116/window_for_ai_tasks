# Task: phase-9-12 — End-to-End Verification & Docs

## Goal
Провести 12 сценариев проверки по всем обязательным фичам xlsx и задокументировать результат. Обновить `CLAUDE.md`, `README_proj.md` и создать `tasks_done/phase-9-done.md`.

## Context
См. `PLAN_chat_agents.md` раздел 13. Это финальная точка phase-9, которая валидирует: 10 обязательных фич xlsx + ручной выбор модели + markdown рендеринг.

## Scope

### 1. Предусловия
- `make up` → стек поднят, healthchecks зелёные.
- `export OWUI_ADMIN_TOKEN=<...>` → токен получен.
- `make deploy-functions` → функции загружены и включены в Admin UI.
- В OpenWebUI создан test-юзер.
- Модель `MWS GPT Auto 🎯` видна в dropdown.

### 2. Прогнать сценарии (таблица — см. `PLAN_chat_agents.md` §13)

Для каждого сценария зафиксировать в `tasks_done/phase-9-done.md`:
- Номер и название.
- Использованные субагенты (из `<details>` блока).
- Модели, которые были вызваны (из Langfuse трассы).
- Время ответа.
- Скриншот или короткий текст результата.
- Verdict: ✅ / ❌ / ⚠️.

### 3. Проверки Langfuse
- Каждый запрос через "MWS GPT Auto" создаёт иерархическую трассу с родителем `mws-auto` и детьми `sa_*`.
- Ручной выбор `mws/deepseek-r1-32b` создаёт прямую трассу без parent'а.

### 4. Обновить `GPTHub_features_template.xlsx`
- Все 10 обязательных → "Да" в колонке "Сделано".
- В колонке "Как и через что работает" указать: `pipelines/auto_router_function.py → sa_<kind> → mws/<model>`.
- #13 и #14 (доп.) — "Stub в v1, полная в v2".

### 5. Обновить документацию
- **`CLAUDE.md`** — добавить раздел "## Auto-Router":
  - Упоминание `pipelines/auto_router_function.py`.
  - Ссылки на `PLAN_chat_agents.md` и `model_capabilities.md`.
  - Как задеплоить: `make deploy-functions`.
  - Когда не вызывается: если выбран конкретный `mws/*`.
- **`README_proj.md`** — раздел "Как пользоваться MWS GPT Auto":
  - Как выбрать модель из dropdown.
  - Как переключиться на конкретную модель вручную.
  - Какие фичи поддерживаются (10+2).
- **`tasks_done/phase-9-done.md`** — резюме phase-9:
  - Что сделано (ссылки на phase-9-1..12).
  - Результаты верификации.
  - Known issues / техдолг.
  - Ссылка на v2 бэклог (Deep Research, Presentation).

## Files
- `tasks_done/phase-9-done.md` (создать)
- `CLAUDE.md` (изменить)
- `README_proj.md` (изменить)
- `GPTHub_features_template.xlsx` (изменить — галочки "Сделано")

## Acceptance criteria
1. Все 10 обязательных сценариев (#1-10 из xlsx) работают end-to-end через "MWS GPT Auto".
2. Сценарий #11 (ручной выбор модели) работает — Pipe не активируется при выборе `mws/*`.
3. Сценарий #12 (markdown) — таблицы и code блоки рендерятся в UI.
4. Langfuse показывает иерархическую трассу для auto-сценариев и плоскую для ручных.
5. `GPTHub_features_template.xlsx` обновлён — 10 обязательных "Да".
6. `tasks_done/phase-9-done.md` содержит результаты всех 12 сценариев.
7. `CLAUDE.md` и `README_proj.md` описывают роутер и способ его использования.

## Dependencies
- phase-9-9 (агрегатор собран).
- phase-9-10 (деплой скрипт).
- phase-9-11 (stub'ы для доп. фич).

## Out of scope
- Load testing.
- Обработка edge cases вне 12 сценариев.
- v2 фичи (Deep Research, Presentation).
