# phase-12-7 — Интеграция в `pipe()`: двухфазная оркестрация

## Цель
Добавить в `Pipe.pipe()` фазу 1.5 — вызов `_sa_fact_check` между основной `asyncio.gather` и `_stream_aggregate`.

## Что сделать
- В `pipelines/auto_router_function.py` в классе `Pipe`:
  1. Реализовать `_should_fact_check`:
     ```python
     def _should_fact_check(
         self, plan: list[SubTask], detected: DetectedInput
     ) -> bool:
         if not self.valves.fact_check_enabled:
             return False
         if _FACT_CHECK_TRIGGER_RE.search(detected.last_user_text or ""):
             return True
         kinds = {t.kind for t in plan}
         return bool(kinds & _CHECKABLE_KINDS)
     ```
  2. В теле `pipe()` после строки, где уже лежит
     `results = await self._maybe_reclassify_stt(...)` (см. `_dispatch`
     → `_maybe_reclassify_stt`), добавить:
     ```python
     if self._should_fact_check(plan, detected):
         user_q = detected.last_user_text or ""
         fc = await self._sa_fact_check(results, detected, user_q)
         # Даже при error=timeout добавляем в results — аггрегатор увидит summary
         # и сможет упомянуть, что проверка не завершилась
         results.append(fc)
         if self.valves.debug:
             print(f"fact_check done: {fc.summary}")
     ```
  3. В блок routing-decision (`<details>🎯 Routing decision</details>`), который собирается в `_stream_aggregate` / начале `pipe()`, дописать строку с субагентами фазы 1.5, если fact_check запущен. Для простоты — добавить его в вывод `Subagents: [...]` как `fact_check` (последним элементом). Проверить, что плэйсхолдер `Subagents:` формируется именно из `plan`, и добавить отдельную строку под ним: `Verifiers: ['fact_check']` когда `fc` существует.
- Гарантировать, что любой exception внутри фазы 1.5 проглатывается: даже полная ошибка фактчекера не должна уронить основной ответ. Все `raise` внутри `_sa_fact_check` уже обёрнуты в `try/except`, но добавить внешний `try/except Exception` в `pipe()` на всякий случай.

## Критерии готовности
- Обычный «привет» → `plan=[sa_ru_chat]`, `_should_fact_check=False`, фаза 1.5 пропускается, в логах `fact_check: skip (no checkable kinds)`.
- «Что происходит сейчас в мире» → `plan=[sa_web_search]`, фаза 1.5 запускается, в routing decision появляется строка `Verifiers: ['fact_check']`, ответ приходит нормально.
- Принудительный триггер «проверь факты» над обычным general-ответом → фаза 1.5 тоже запускается.
- Симулированный краш в `_sa_fact_check` (`raise RuntimeError("boom")` во время теста) → пайп всё равно отвечает пользователю, в логах `fact_check FAILED`.

## Затронутые файлы
- `pipelines/auto_router_function.py`
