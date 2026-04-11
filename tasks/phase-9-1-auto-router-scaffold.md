# Task: phase-9-1 — Auto-Router Scaffold

## Goal
Создать каркас Pipe-функции OpenWebUI `auto_router_function.py`, которая появляется в выпадающем списке моделей как `MWS GPT Auto 🎯` и принимает запросы — пока с заглушкой вместо логики.

## Context
Это первый шаг phase-9. Остальные задачи (детектор, классификатор, субагенты) будут дописывать эту функцию. См. `PLAN_chat_agents.md` разделы 2, 4, 9.

## Scope
- Файл `pipelines/auto_router_function.py`.
- Класс `Pipe` с:
  - `class Valves(BaseModel)` — поля: `litellm_base_url` (default `http://litellm:4000/v1`), `litellm_api_key` (из env `LITELLM_MASTER_KEY` или Valve), `classifier_model` (default `mws/gpt-oss-20b`), `default_ru_model` (default `mws/t-pro`), `default_en_model` (default `mws/gpt-alpha`), `enabled` (default True), `debug` (default False).
  - `def pipes(self)` → `[{"id": "mws-auto", "name": "MWS GPT Auto 🎯"}]`.
  - `async def pipe(self, body, __user__=None, __request__=None)` — для MVP возвращает строку `"👋 MWS GPT Auto router is live. Received {len(messages)} messages and {len(files)} files."`.
- Docstring в начале файла с `title`, `author`, `version`, `description` — как в `memory_function.py`.

## Files
- `pipelines/auto_router_function.py` (создать)

## Acceptance criteria
1. Файл синтаксически валиден: `python -m py_compile pipelines/auto_router_function.py` проходит.
2. После ручного деплоя через Admin UI (Functions → Create → paste code → save → enable) в dropdown моделей появляется `MWS GPT Auto 🎯`.
3. Выбрав эту модель и отправив "hello", пользователь получает ответ `"👋 MWS GPT Auto router is live. Received 1 messages and 0 files."`.
4. Функция не вызывает LiteLLM — только возвращает стабильный строковый ответ.

## Dependencies
- Нет.

## Out of scope
- Любая логика детекции, классификации, вызова моделей.
- Скрипт автоматического деплоя (phase-9-10).
- Streaming (phase-9-9).
