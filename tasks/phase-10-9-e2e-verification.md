# Task: phase-10-9 — End-to-End Verification

## Goal
Прогнать целиком сценарии, описанные в `PLAN_db_memory.md` (раздел Verification), и убедиться, что новая память диалогов работает end-to-end: запись эпизодов, семантический recall, временной recall, user-scoping, персистентность через restart, неломание существующих факт-мемори.

## Context
Все предыдущие phase-10 задачи (10-1 .. 10-8) должны быть закоммичены. Эта задача — финальная проверка перед маркировкой фичи как done. Результаты сохранить в `tasks_done/phase-10-done.md` с датами, логами ключевых SQL-запросов и скриншотами (если есть).

## Scenarios

### S1 — Эмбеддинги живые
```
docker compose exec memory-service python -c \
  "import asyncio; from app.embedding import get_embedding; \
   v=asyncio.run(get_embedding('привет мир')); print(len(v), v[:3])"
```
Ожидаемо: `1024` + три ненулевых разных float. Никаких warnings про hash-fallback.

### S2 — Эпизоды пишутся
1. Создать нового пользователя в OpenWebUI (или использовать существующего).
2. Поговорить 5 сообщений на одну тему (например «как настроить nginx reverse proxy»).
3. `docker compose exec postgres psql -U mws -d memory -c \
   "select user_id, chat_id, turn_end_at, summary from conversation_episodes order by created_at desc limit 3;"`
4. Ожидаемо: свежая строка с разумным summary про nginx.

### S3 — Семантический recall
1. В том же аккаунте сделать ещё 2 разных диалога (python async, рецепт борща), каждый ≥5 сообщений. Каждый даст ≥1 эпизод.
2. Открыть новый чат, спросить: «о чём мы обсуждали сетевую настройку?»
3. Ожидаемо:
   - В логах openwebui (`docker compose logs openwebui | grep memory_recall`) виден `kind=memory_recall`.
   - В финальном ответе пользователю — упоминание даты и темы nginx.
   - Темы борща/async не в топе.

### S4 — Временной recall
1. `docker compose exec postgres psql -U mws -d memory -c \
   "update conversation_episodes set turn_end_at = now() - interval '95 days' \
    where summary ilike '%nginx%' returning id;"`
2. В новом чате спросить: «о чём мы говорили 3 месяца назад?»
3. Ожидаемо:
   - Классификатор выдаёт `time_window` около `now-90d` (видно в debug-логах router).
   - Recall возвращает именно сдвинутый nginx-эпизод, не python/борщ.

### S5 — User scoping
1. Создать второго пользователя в OpenWebUI.
2. Из его аккаунта спросить «о чём мы обсуждали nginx?».
3. Ожидаемо: subagent вернул «ничего не найдено». Чужие эпизоды не утекают.

### S6 — Персистентность через restart
1. `docker compose down && docker compose up -d`.
2. Подождать пока поднимется стек.
3. `select count(*) from conversation_episodes;` — то же число, что до restart.
4. Повторить S3 — результат тот же.
5. Проверить, что `./data/postgres` существует на хосте и не пустой.

### S7 — Факты не сломаны
1. `select count(*) from memories where user_id='<test user>';` — запомнить число.
2. Поговорить 5 сообщений про новую привычку.
3. Повторить select — счётчик вырос.
4. В новом чате inlet продолжает инжектить факты в system prompt (проверить через debug-лог memory-service или через поведение бота).

### S8 — Существующие группы A/B auto-router не сломаны
Прогнать тесты из `tasks_done/phase-9-done.md` группы A (chat/routing) и B (classifier) — ≥9/10 должны пройти без регрессий.

## Files
- `tasks_done/phase-10-done.md` (new) — финальный отчёт с датами, логами и пометками pass/fail по каждому сценарию.

## Acceptance criteria
- Все сценарии S1-S8 дают ожидаемые результаты.
- В `tasks_done/phase-10-done.md` зафиксированы: дата прогона, версия (git commit hash), SQL-вывод ключевых запросов, список pass/fail.
- Если какие-то сценарии проваливаются — создать багфикс-задачу `phase-10-10-...md` и описать там конкретику.

## Dependencies
- phase-10-1 .. phase-10-8 — все закоммичены.

## Out of scope
- Нагрузочное тестирование recall (latency, throughput) — это v2.
- Deep-fetch raw messages из openwebui.chat — v2.
