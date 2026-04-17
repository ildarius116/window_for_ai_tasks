# phase-12-8 — Инъекция `FactCheckReport` в `_stream_aggregate`

## Цель
Научить аггрегатор использовать результаты фактчекера: (а) передавать меткам (1)/(2)/(3) в финальный ответ, (б) дописывать внизу свёрнутый блок «Проверка источников» в формате `<details>`.

## Что сделать
- В `pipelines/auto_router_function.py`, в `Pipe._stream_aggregate`:
  1. В начале метода извлечь fact-check отдельно от остальных результатов:
     ```python
     fc_result = next((r for r in results if r.kind == "fact_check"), None)
     checkable_results = [r for r in results if r.kind != "fact_check"]
     ```
     Все дальнейшие операции над `results` (сбор contexts, citations, has_artifacts) делать по `checkable_results`, чтобы fact_check не попал в корпус, из которого аггрегатор «пишет ответ».
  2. Построить блок системного сообщения и добавить его в `system_prompt` аггрегатора **только** если `fc_result` есть и `fc_result.metadata.get("report")` непустой:
     ```python
     def _format_fact_check_for_prompt(report: dict) -> str:
         urls = report.get("urls", [])
         claims = report.get("claims", [])
         ok = sum(1 for u in urls if u.get("status") in ("url_ok", "url_redirect"))
         broken = sum(1 for u in urls if u.get("status") in ("url_404", "url_unreachable"))
         lines = [
             "--- FACT-CHECK REPORT ---",
             f"URLs checked: {ok} ok, {broken} broken, {len(urls)} total.",
             "Claims:",
         ]
         emoji = {"proven": "✅ (1)", "plausible": "⚠️ (2)", "fabricated": "❌ (3)", "unknown": "❓ (?)"}
         for c in claims:
             tag = emoji.get(c.get("verdict", "unknown"), "❓ (?)")
             ev = f" — via {c['evidence_url']}" if c.get("evidence_url") else ""
             lines.append(f"  {tag} «{c.get('text','')}»{ev}")
         lines.append("---")
         lines.append(
             "Используй эти метки в ответе. Не повторяй утверждения с меткой ❌ (3). "
             "Для ⚠️ (2) явно пиши «по некоторым источникам». "
             "Для ✅ (1) можешь цитировать и прикладывать evidence_url."
         )
         return "\n".join(lines)
     ```
     И прилепить результат к `system_prompt` аггрегатора через `\n\n`.
  3. В конце стрима, ПОСЛЕ того как отработал `_scrub_artifact_echoes` и после `_render_artifacts`, но ДО последнего `yield`, собрать пользовательский details-блок:
     ```python
     def _render_fact_check_details(report: dict) -> str:
         claims = report.get("claims", [])
         urls = report.get("urls", [])
         if not claims and not urls:
             return ""
         rows = []
         for c in claims:
             v = c.get("verdict", "unknown")
             em = {"proven":"✅","plausible":"⚠️","fabricated":"❌","unknown":"❓"}[v]
             line = f"- {em} **{v}** — {c.get('text','')}"
             if c.get("evidence_url"):
                 line += f"  \n  ↳ {c['evidence_url']}"
             rows.append(line)
         bad_urls = [u for u in urls if u.get("status") in ("url_404","url_unreachable")]
         bad_lines = [f"- ❌ {u.get('url')} ({u.get('status')})" for u in bad_urls]
         body = "\n".join(rows)
         if bad_lines:
             body += "\n\n**Недоступные URL:**\n" + "\n".join(bad_lines)
         return f"\n\n<details><summary>✅ Проверка источников</summary>\n\n{body}\n\n</details>"
     ```
  4. Выдать details-блок как последний `yield` после основного контента и artifact-блоков.
- Убедиться, что существующие regex-стрипы (`_FILE_LINK_RE`, `_IMAGE_LINK_RE`, `_DETAILS_BLOCK_RE`) не матчат НАШ новый `<details>` — они не должны (scrub применяется к истории/буферу ДО этого yield, не к финальному выводу).

## Критерии готовности
- Запрос «Мисс Мира 2025» → в финальном ответе присутствуют метки ✅/⚠️/❌ в соответствии с тем, что реально нашли по URL.
- В конце ответа есть свёрнутый блок «✅ Проверка источников» с URL и вердиктами.
- При `fc.error=timeout` аггрегатор получает пустой отчёт → метки не появляются, details-блок не рисуется (или рисуется с «проверка не завершилась»).
- Существующие сценарии без checkable-субагентов не изменились.

## Затронутые файлы
- `pipelines/auto_router_function.py`
