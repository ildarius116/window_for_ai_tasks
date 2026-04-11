# Task: phase-10-5 — Memory Function Outlet: Write Episodes

## Goal
Дополнить `pipelines/memory_function.py` так, чтобы в `outlet()` параллельно с существующим `/memories/extract` дёргался новый `/episodes` эндпоинт и писался эпизод последних ~8 сообщений текущего чата.

## Context
`outlet()` уже throttle-ится раз в 4 пользовательских сообщения (`pipelines/memory_function.py:100-126`). Не меняем throttle — просто добавляем второй POST. Факт-экстракция остаётся как есть (пользователь подтвердил, что оба слоя работают параллельно).

## Scope
- В `outlet()` после успешного POST `/memories/extract`:
  1. Вырезать последние 8 сообщений (или меньше, если их нет столько).
  2. Определить `chat_id` из `body.get("chat_id")` или `__metadata__.get("chat_id")` (проверить актуальную форму OpenWebUI body — она могла измениться).
  3. Определить `user_id` из `__user__.get("id")`.
  4. Вычислить `message_indices = [len(all_messages)-window_size, len(all_messages)]`.
  5. `turn_start_at`/`turn_end_at`:
     - если у сообщений есть `timestamp` — min/max;
     - иначе `datetime.now(UTC)` для обоих.
  6. POST на `http://memory-service:8000/episodes` с body (`messages` — в формате `[{"role","content"}]`, без вложений).
- Ошибка `/episodes` **не** должна ломать `outlet()` — лог в debug, вернуть `body` как обычно. Смысл: если memory-service лежит, чат продолжает работать.
- Ошибка `/memories/extract` обрабатывается как раньше — не трогаем.

## Files
- `pipelines/memory_function.py` (изменить)

## Acceptance criteria
1. После `docker compose restart bootstrap` функция `mws_memory` в таблице `function` содержит новую версию (`select length(content) from function where id='mws_memory';` растёт).
2. Поговорить с ботом 5 сообщений → в логах `memory-service` видно POST `/episodes` с 200.
3. `select summary, turn_end_at, chat_id from conversation_episodes order by created_at desc limit 1;` показывает свежую строку с корректным `chat_id` и summary про тему диалога.
4. Поговорить с ботом ещё 4 сообщения → появляется второй эпизод (throttle сработал).
5. При остановленном memory-service (`docker compose stop memory-service`) чат продолжает отвечать пользователю, а ошибка видна только в debug-логах openwebui.
6. Существующая таблица `memories` продолжает наполняться (факт-экстракция не сломана).

## Dependencies
- phase-10-3, phase-10-4 (оба эндпоинта должны быть рабочими).

## Out of scope
- Дедупликация перекрывающихся окон — v2.
- Ретроактивная индексация старых чатов из OpenWebUI — отдельная задача, если понадобится.
