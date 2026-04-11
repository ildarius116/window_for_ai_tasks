# Task: phase-10-6 — Auto-Router: Plumb user_id into SubTask.metadata

## Goal
Сейчас `pipe()` в `pipelines/auto_router_function.py` принимает `__user__: Optional[dict]`, но никуда его не пробрасывает. Без этого subagent `sa_memory_recall` (phase-10-8) не сможет скопировать `user_id` и recall вернёт чужие воспоминания (или ничего). Надо протащить `user_id` через всю цепочку создания `SubTask`.

## Context
`pipe()` находится примерно на line 134-142. Все места, где создаётся `SubTask(...)`, расположены в `_classify_and_plan` и в rule-based шорт-сёркитах (`_REASONER_RE`, длина-1500, детектор модальностей и т.д.).

## Scope
- В `pipe()`: извлечь `user_id = (__user__ or {}).get("id")` сразу после входа. Прокинуть параметром в `_classify_and_plan(..., user_id=user_id)` (или сохранить в `self._current_user_id` — локально для вызова — любой способ, главное избежать глобальных).
- В `_classify_and_plan`: при создании каждого `SubTask` добавить `metadata={"user_id": user_id, **(extra or {})}`. Пройти все `SubTask(...)` в файле и убедиться, что ни один не теряет metadata.
- Не трогать остальные поля SubTask и не менять сигнатуру `CompactResult`.
- Если `user_id is None` (анонимный вызов, например тест) — класть `None`, subagent phase-10-8 сам обработает этот случай.

## Files
- `pipelines/auto_router_function.py` (изменить — pipe(), _classify_and_plan, все SubTask конструкторы)

## Acceptance criteria
1. После `docker compose restart bootstrap` поговорить с ботом в любом чате → в логах router-а в начале pipe видно `user_id=<uuid>` (временно добавить debug-лог и убрать в конце задачи).
2. Быстрый unit-трюк: добавить временный stub-subagent, который возвращает `task.metadata.get("user_id")` в `summary`, проверить что строка непуста. Удалить stub после проверки.
3. Все существующие subagent'ы (`sa_general`, `sa_ru_chat`, `sa_code`, `sa_reasoner`, `sa_long_doc` и т.д.) продолжают работать без регрессий — прогнать группы A/B из `tasks_done/phase-9-done.md` (короткий RU chat, код, reasoner, long_doc). **Ни один не должен сломаться** от появления лишнего поля в metadata.

## Dependencies
- Нет (можно делать параллельно с phase-10-1..5, но phase-10-8 зависит от этой задачи).

## Out of scope
- Новый subagent `sa_memory_recall` — phase-10-8.
- Классификатор — phase-10-7.
