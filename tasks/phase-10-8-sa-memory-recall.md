# Task: phase-10-8 — sa_memory_recall Subagent

## Goal
Реализовать subagent `_sa_memory_recall`, который дёргает `http://memory-service:8000/episodes/recall` с `user_id` + `query` + опциональным `time_window` и возвращает `CompactResult` с красивым списком найденных эпизодов (summary + дата).

## Context
Dispatch словарь в `_run_subagent` находится примерно на line 530-544 `pipelines/auto_router_function.py`. Паттерн HTTP-вызова к внутренним сервисам видно в `_sa_web_fetch` (httpx.AsyncClient). Budget summary — ≤500 токенов, режется через существующий `_truncate_tokens(..., 500)`.

## Scope
- Новый метод:
  ```python
  async def _sa_memory_recall(self, task: SubTask) -> CompactResult:
      user_id = task.metadata.get("user_id")
      if not user_id:
          return CompactResult(
              kind="memory_recall",
              summary="",
              error="memory_recall: no user_id in metadata",
          )
      payload = {
          "user_id": user_id,
          "query": task.input_text,
          "limit": 5,
      }
      tw = task.metadata.get("time_window") or {}
      if tw.get("from"):
          payload["date_from"] = tw["from"]
      if tw.get("to"):
          payload["date_to"] = tw["to"]
      try:
          async with httpx.AsyncClient(timeout=15) as cli:
              r = await cli.post(
                  "http://memory-service:8000/episodes/recall",
                  json=payload,
              )
              r.raise_for_status()
              episodes = r.json()
      except Exception as e:
          return CompactResult(
              kind="memory_recall",
              summary="",
              error=f"memory_recall request failed: {e}",
          )
      if not episodes:
          return CompactResult(
              kind="memory_recall",
              summary="В истории диалогов ничего не найдено по этому запросу.",
          )
      lines = []
      for ep in episodes:
          date = (ep.get("turn_end_at") or "")[:10]
          lines.append(f"- [{date}] {ep.get('summary','').strip()}")
      body = "Найденные эпизоды из прошлых диалогов:\n" + "\n".join(lines)
      return CompactResult(
          kind="memory_recall",
          summary=self._truncate_tokens(body, 500),
          citations=[ep.get("chat_id") for ep in episodes if ep.get("chat_id")],
      )
  ```
- В dispatch-словаре добавить: `"memory_recall": self._sa_memory_recall`.
- Убедиться, что агрегатор (RU / EN финальный ответ) корректно включает `summary` этого subagent-а в свой итог — существующий паттерн `CompactResult.summary` уже обрабатывается, дополнительная логика не нужна.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. После `docker compose restart bootstrap` и записи нескольких эпизодов (через нормальный чат или напрямую curl-ом из phase-10-3):
   - Запрос «о чём мы говорили про nginx?» даёт ответ, содержащий дату и краткую тему nginx-эпизода.
2. Запрос «что было 3 месяца назад?» (после искусственного сдвига `turn_end_at` в БД) возвращает именно тот старый эпизод.
3. Если `user_id` отсутствует (тестовый вызов) — subagent возвращает `error`, но orchestrator не падает (общий паттерн error-handling из phase-9-4).
4. Если memory-service лежит — subagent возвращает `error`, а финальный ответ пользователю получает честное «не удалось достать историю», а не падает всей цепочкой.
5. `summary` не превышает ~500 токенов даже если эпизодов 5 больших.

## Dependencies
- phase-10-4 (эндпоинт recall).
- phase-10-6 (user_id в metadata).
- phase-10-7 (классификатор раздаёт kind=memory_recall).

## Out of scope
- Deep-fetch исходных сообщений из `openwebui.chat` — v2.
- Reranking через LLM — v2.
