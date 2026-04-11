# Task: phase-10-7 — Classifier: memory_recall Intent + time_window

## Goal
Научить классификатор auto-router распознавать запросы типа «о чём мы говорили 3 месяца назад» / «what did we discuss last week» и возвращать интент `memory_recall` + опциональное поле `time_window: {from, to}` в ISO-формате. Плюс регекс-шорт-сёркит для самых типичных формулировок — он дешевле и надёжнее LLM на коротких фразах.

## Context
Классификатор находится в `pipelines/auto_router_function.py` около line 386-399 (system prompt JSON-mode на `mws/gpt-oss-20b`), с `kind_map` на line ~424-433. Рядом `_REASONER_RE` (line ~83) — шаблон для добавления своего регекса. Порядок шорт-сёркитов важен: recall должен срабатывать **до** length-1500 → long_doc, иначе длинные вопросы про историю уйдут не туда.

## Scope
- Добавить регекс рядом с `_REASONER_RE`:
  ```python
  _MEMORY_RECALL_RE = re.compile(
      r"(?i)("
      r"о ч[её]м мы говорили|когда мы говорили|что я тебе (рассказывал|говорил)|"
      r"помнишь,?\s+(как|что)|на прошлой неделе|"
      r"\d+\s+(час|день|дня|дней|недел[юяи]|месяц[ае]?в?|год[а]?|лет)\s+назад|"
      r"what did we (discuss|talk about)|do you remember|"
      r"last\s+(week|month|year)|a\s+(week|month|year)\s+ago"
      r")"
  )
  ```
- В `_classify_and_plan` поставить проверку **перед** length-1500 и **перед** `_REASONER_RE`:
  ```python
  if _MEMORY_RECALL_RE.search(detected.last_user_text):
      plan.append(SubTask(
          kind="memory_recall",
          input_text=detected.last_user_text,
          metadata={"user_id": user_id},  # + time_window если достали из LLM позже
      ))
      return plan
  ```
- В system-prompt классификатора (line ~386-399) добавить:
  - В список валидных интентов: `memory_recall`.
  - Инструкцию: «If the user asks about past conversations (e.g. 'what did we discuss', 'о чём мы говорили'), set intent=memory_recall. If they mention a time marker (e.g. '3 months ago', 'last week', 'вчера'), ALSO return `time_window: {"from": "<ISO-8601>", "to": "<ISO-8601>"}` based on the current date. Otherwise omit time_window.»
  - Пример в prompt:
    ```json
    {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-01-11T00:00:00Z","to":"2026-01-18T23:59:59Z"}}
    ```
- В `kind_map` добавить `"memory_recall": "memory_recall"`.
- После парсинга ответа классификатора: если есть `time_window`, положить его в `SubTask.metadata["time_window"]`. Иначе не класть (subagent отправит запрос без дат).
- Current date классификатор берёт из текущего system prompt — добавить `f"Current date: {datetime.now(UTC).date().isoformat()}"` в system, чтобы «3 месяца назад» считался относительно сегодня.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. Регекс-юнит: `_MEMORY_RECALL_RE.search("о чём мы говорили неделю назад")` matches; `"как приготовить борщ"` — нет; `"what did we discuss last month"` — matches.
2. В чате отправить «о чём мы говорили?» — в логах router-а `kind=memory_recall`, subagent заглушкой возвращает хоть что-то (реальный subagent появится в phase-10-8).
3. В чате отправить «что я тебе рассказывал 3 месяца назад про nginx» — классификатор (LLM fallback) выдаёт `time_window` с датами около `now-90d`. Виден в debug-логах.
4. Короткий чат «привет» продолжает идти в `sa_ru_chat`, не в memory_recall.
5. Длинный (>1500 chars) вопрос про историю уходит в `memory_recall`, а не в `long_doc` (проверка порядка шорт-сёркитов).

## Dependencies
- phase-10-6 (user_id уже должен лежать в metadata).

## Out of scope
- Сам subagent — phase-10-8.
- Поддержка относительных выражений в самом memory-service — не нужна, классификатор уже выдаёт ISO.
