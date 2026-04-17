# phase-12-6 — Оркестратор `_sa_fact_check`

## Цель
Собрать все четыре шага (dedupe URL → validate → extract claims → verdict) в один метод `_sa_fact_check`, возвращающий `CompactResult(kind="fact_check")`.

## Что сделать
- В `pipelines/auto_router_function.py` заменить заглушку из phase-12-1 на:
  ```python
  async def _sa_fact_check(
      self,
      results: list[CompactResult],
      detected: DetectedInput,
      user_question: str,
  ) -> CompactResult:
      checkable = [r for r in results if r.kind in _CHECKABLE_KINDS and not r.error]
      if not checkable:
          return CompactResult(kind="fact_check", summary="nothing to check")

      async def _do() -> FactCheckReport:
          urls = self._dedupe_urls(checkable, self.valves.fact_check_max_urls)
          # phase 1: url validation + claim extraction параллельно — независимы
          url_task = asyncio.create_task(self._validate_urls(urls)) if urls else None
          claim_task = asyncio.create_task(self._extract_claims(checkable, user_question))
          url_statuses = await url_task if url_task else []
          claims = await claim_task
          # phase 2: verdict — нужны оба результата
          claims_with_verdict = await self._verdict_claims(claims, url_statuses, user_question)
          return FactCheckReport(
              urls=url_statuses,
              claims=claims_with_verdict,
              total_checked_kinds=sorted({r.kind for r in checkable}),
          )

      try:
          report = await asyncio.wait_for(_do(), timeout=self.valves.fact_check_timeout)
      except asyncio.TimeoutError:
          return CompactResult(
              kind="fact_check",
              summary="fact-check timed out",
              error="timeout",
          )
      except Exception as e:
          print(f"fact_check FAILED: {type(e).__name__}: {e}")
          return CompactResult(
              kind="fact_check",
              summary=f"fact-check failed: {type(e).__name__}",
              error=str(e)[:200],
          )

      # Короткий summary для логов (полный отчёт — в metadata)
      total = len(report.claims)
      proven = sum(1 for c in report.claims if c.verdict == "proven")
      fab = sum(1 for c in report.claims if c.verdict == "fabricated")
      url_ok = sum(1 for u in report.urls if u.status in ("url_ok", "url_redirect"))
      summary = (
          f"fact-check: {proven}/{total} proven, {fab} fabricated, "
          f"URLs ok={url_ok}/{len(report.urls)}"
      )
      return CompactResult(
          kind="fact_check",
          summary=summary,
          metadata={"report": asdict(report)},
      )
  ```
- Импортировать `asdict` из `dataclasses`, если ещё не импортирован.
- `CompactResult.metadata["report"]` — словарь, чтобы его можно было без проблем логировать/сериализовать и передать в `_stream_aggregate`.

## Критерии готовности
- На ручном вызове `await pipe._sa_fact_check([fake_web_search_result], detected, "Мисс Мира 2025")` возвращает `CompactResult(kind="fact_check")` с заполненными `metadata["report"]`.
- На пустом `results` → мгновенный возврат с summary `"nothing to check"`, без LLM-вызовов.
- На общем таймауте 15s → возврат с `error="timeout"`, пайп не зависает.

## Затронутые файлы
- `pipelines/auto_router_function.py`
