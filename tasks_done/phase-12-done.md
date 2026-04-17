# Phase 12 — Attribution-checker subagent (DONE, 2026-04-17)

Goal: add a runtime `_sa_fact_check` subagent to `pipelines/auto_router_function.py` that validates URLs and factual claims returned by other subagents before the aggregator streams the final answer to the user.

The observed trigger: the «Мисс Мира 2025» query on 2026-04-17 returned a deep_research answer citing `[1] wikipedia.org/...` and `[2] rbc.ru/...` without any runtime guarantee that those URLs resolve or that the named person (Opal Suchata Chuangsri), date (31 May 2025), venue (HITEX Hyderabad), or crown-handover claim exist in the evidence. `.claude/agents.json` already had a dev-time `FactCheckerAgent`, but the runtime pipe had no equivalent — hence the user could not see the new subagent in the routing-decision block.

## What shipped

### 1. `pipelines/auto_router_function.py`

#### Dataclasses (phase-12-2)
- `UrlStatus(url, status, http_code, final_url, snippet, error)` — one per validated URL. `status ∈ {url_ok, url_redirect, url_404, url_auth_required, url_unreachable, url_blocked_ssrf}`.
- `Claim(text, source_kind, verdict, evidence_url, reason)` — one per extracted factual assertion. `verdict ∈ {proven, plausible, fabricated, unknown}`.
- `FactCheckReport(urls, claims, total_checked_kinds, error)` — the payload stored in `CompactResult.metadata["report"]`.

#### Constants (phase-12-2)
- `_CHECKABLE_KINDS = {"web_search", "web_fetch", "deep_research", "memory_recall", "doc_qa"}` — activation set. `general`, `ru_chat`, `code`, `reasoner`, `long_doc`, `vision`, `stt`, `image_gen`, `presentation` are **not** checked — either no verifiable claims (code/images) or verification cost is not worth it (chat).
- `_FACT_CHECK_TRIGGER_RE` — matches «проверь факты», «fact-check», «verify (the) claims/sources/facts». Fires even for plans not in `_CHECKABLE_KINDS`.
- `_SSRF_BLOCK_RE` — blocks `localhost`, `127.*`, `10.*`, `169.254.*`, `192.168.*`, `172.16–31.*` from URL validation (checked in both `_dedupe_urls` and `_validate_urls` for defense in depth).

#### Valves (phase-12-1)
Six new fields on `Pipe.Valves`, all user-editable from the OpenWebUI Admin → Functions → MWS GPT Auto 🎯 → Valves panel:
- `fact_check_enabled=True` — master switch.
- `fact_check_timeout=15.0` — global deadline for the entire phase-1.5 step.
- `fact_check_max_urls=12` — caps URL validation fan-out.
- `fact_check_max_claims=6` — caps claim extraction output.
- `fact_check_model="mws/kimi-k2"` — verdict LLM.
- `fact_check_claim_model="mws/gpt-oss-20b"` — claim extractor LLM.

#### Helpers (phase-12-3, phase-12-4, phase-12-5)
- `_dedupe_urls(results, max_urls) -> list[str]` — static: collects URLs from each checkable `CompactResult.citations` + `_URL_RE.findall(summary)`, trims punctuation tail (`).,;:]»\"'`), drops SSRF-blocked, caps at `max_urls`.
- `async _validate_urls(urls) -> list[UrlStatus]` — `httpx.AsyncClient(follow_redirects=False, http2=False)` + `Semaphore(8)` + `Timeout(5.0, connect=3.0)`. HEAD first; on 2xx pulls a 2048-char GET snippet; on 3xx follows once for snippet; on 401/403 returns `url_auth_required` (so bot-gated sites like reddit/twitter don't get labeled fabricated); on 4xx → `url_404`; on 5xx/ConnectError/TimeoutException → `url_unreachable` with `error` field filled.
- `async _extract_claims(results, user_question) -> list[Claim]` — calls `mws/gpt-oss-20b` with `response_format={"type":"json_object"}`, `temperature=0`, `max_tokens=600`. System prompt explicitly forbids including opinions or question paraphrases, demands full-sentence claims about people/events/dates/numbers/addresses/URLs/quotes. Drops claims with `len(text) < 10`. Returns `[]` on LiteLLM failure (non-fatal).
- `async _verdict_claims(claims, url_statuses, user_question) -> list[Claim]` — calls `mws/kimi-k2` with JSON mode, `max_tokens=900`. Evidence set built from `url_ok`/`url_redirect` snippets (2 KB each) + a "НЕДОСТУПЕН" note for `url_404`/`url_unreachable`. **Critical guard**: if the LLM returns `verdict=proven` but `evidence_url` is empty or not in `url_ok_set`, it's forced down to `plausible` and `evidence_url` is cleared. This prevents the verdict LLM from hallucinating a proven URL that wasn't actually verified. On LLM failure every claim returns with `verdict="unknown"` and `reason="verdict_llm_failed"`.

#### Orchestrator (phase-12-6)
`async _sa_fact_check(results, detected, user_question) -> CompactResult`:
1. Filter `checkable = [r for r in results if r.kind in _CHECKABLE_KINDS and not r.error]`.
2. If empty → return `CompactResult(kind="fact_check", summary="nothing to check")` immediately (no LLM calls).
3. Inner `_do()`: `asyncio.create_task(_validate_urls)` + `asyncio.create_task(_extract_claims)` in parallel (they're independent); await both; then `_verdict_claims(claims, url_statuses, user_question)` sequentially (needs both inputs).
4. Wrap entire `_do()` in `asyncio.wait_for(..., timeout=valves.fact_check_timeout)`.
5. On `asyncio.TimeoutError` → `CompactResult(..., summary="fact-check timed out", error="timeout")`.
6. On any other exception → `CompactResult(..., summary="fact-check failed: ...", error=str(e)[:200])`, printing `fact_check FAILED: <Type>: <msg>` to stdout.
7. On success → build a short summary `f"fact-check: {proven}/{total} proven, {fab} fabricated, URLs ok={url_ok}/{len(urls)}"` and stash the full `FactCheckReport` as `asdict(report)` in `metadata["report"]`.

#### pipe() integration (phase-12-7)
- `_should_fact_check(plan, detected) -> bool` — returns `valves.fact_check_enabled AND (_FACT_CHECK_TRIGGER_RE.search(last_user_text) OR plan kinds & _CHECKABLE_KINDS)`.
- In `pipe()`, immediately after `_maybe_reclassify_stt` and before `final_model = ...`:
  ```python
  if self._should_fact_check(plan, detected):
      try:
          fc = await self._sa_fact_check(results, detected, detected.last_user_text or "")
          results.append(fc)
          if self.valves.debug:
              print(f"[mws-auto {trace_id[:8]}] fact_check done: {fc.summary}")
      except Exception as e:
          print(f"fact_check phase FAILED: {type(e).__name__}: {e}")
  ```
  The outer try/except is a belt-and-suspenders guard — `_sa_fact_check` already catches inside.
- `_format_routing_block` gained an `include_verifier: bool = False` parameter and prints `- **Verifiers:** ['fact_check']` under `Subagents:` when both the flag and `_should_fact_check` are true. The single call site in `pipe()` passes `include_verifier=True`.

#### Aggregator integration (phase-12-8)
- `_stream_aggregate` now extracts `fc_result = next(... kind == "fact_check" ...)` at the top and builds the scratchpad from `checkable_results = [r for r in results if r.kind != "fact_check"]` — so the aggregator doesn't treat the fact-check summary as subagent output to paraphrase.
- New `@staticmethod _format_fact_check_for_prompt(report)` — renders `--- FACT-CHECK REPORT ---` with per-claim `✅ (1) / ⚠️ (2) / ❌ (3) / ❓ (?)` labels, URL counts, and a trailing instruction: "Не повторяй утверждения с меткой ❌ (3). Для ⚠️ (2) явно пиши «по некоторым источникам». Для ✅ (1) можешь цитировать и прикладывать evidence_url." Appended to `system_prompt` right before `final_messages` is built, only if `fc_result` exists and `not fc_result.error`.
- New `@staticmethod _render_fact_check_details(report)` — renders a user-facing `<details>\n<summary>✅ Проверка источников</summary>` block with per-claim rows (emoji + verdict + text + optional evidence_url) and a "Недоступные URL:" section listing broken links. Returns empty string when the report has no claims and no URLs.
- `pipe()` yields the details block last, after `artifact_md`. Only rendered when `fc and not fc.error and report has content`.

### 2. `scripts/e2e_fact_check_test.py` (phase-12-9)
Covers F1–F7 from `tasks/phase-12-9-e2e-verification.md`:
- **F1 live** — Miss World 2025 query → asserts `Verifiers` line present in routing block and details block rendered.
- **F2 live** — Messi-2012-Olympics false-fact trap → asserts `❌` or `⚠️` present in the details block (he didn't play 2012).
- **F3 live** — `"Проверь факты в этом тексте: Земля плоская..."` → asserts `Verifiers` present even though plan would normally be `[general]`.
- **F4 live** — «Привет, как дела?» → asserts `Verifiers` **absent** and details block absent (regression guard for smalltalk).
- **F5 hermetic** — unit-style call to `pipe._sa_fact_check` with a mocked `CompactResult(kind="web_search", citations=["https://nosuch-domain-q8xr5.example/..."], ...)` → asserts at least one `UrlStatus.status == "url_unreachable"`. Always runs.
- **F6 hermetic** — sets `valves.fact_check_timeout=0.01`, calls `_sa_fact_check` with a real URL → asserts returned `CompactResult.error == "timeout"`. Always runs.
- **F7 live** — `"Сделай презентацию про Python async/await на 5 слайдов"` → asserts `Verifiers` **absent** (presentation not in `_CHECKABLE_KINDS`, fact-check correctly skipped). Regression for phase-11.

`SKIP_LIVE=1` env flag skips F1/F2/F3/F4/F7 so the hermetic F5/F6 can be exercised without a live MWS GPT API key. Run pattern matches `scripts/e2e_memory_test.py`: `docker cp` the script + `auto_router_function.py` into the openwebui container, then `docker exec ... python /tmp/fc.py`.

### 3. `CLAUDE.md`
- Development Conventions gained a fact-checker paragraph covering activation, pipeline stages, guards, and the master switch.
- Key Files gained `PLAN_truth_agent.md` and `scripts/e2e_fact_check_test.py`.
- Project Status gained a dedicated "Phase 12 — Fact-checker subagent" block ahead of the "Remote server deployment fixes" section, including rationale, architecture, activation rules, valve names, and the critical `proven→evidence_url ∈ url_ok_set` guard.

### 4. `PLAN_truth_agent.md`
Full design doc: context, architecture decision (two-phase over tool-calling inside aggregator, because streaming + context-isolation), per-component deltas, risks + mitigations (SSRF, false-positive 403s via `url_auth_required`, latency +3–6 s mitigated by hard deadline, cost mitigated by cheap `gpt-oss-20b` for extraction + activation filter), and explicit "not in scope" list.

## Key architectural decisions

1. **Phase 1.5, not parallel to subagents.** The fact-checker verifies the *output* of other subagents (their URLs and claims). Running it in the same `asyncio.gather` as the plan would give it only the user's question, not the results — that's just another web_search, not verification.
2. **Activation by plan composition, not by LLM classifier.** Lesson from phase-10 memory_recall regressions: LLM classifiers sometimes silently miss intents. `_CHECKABLE_KINDS` is a hard rule that runs post-plan, so as long as any checkable subagent is in the plan, fact-check fires. The trigger regex is a separate override for explicit user requests.
3. **Context-isolation invariant preserved.** The fact-checker receives only `CompactResult.summary` (≤500 tokens) and `CompactResult.citations` from other subagents — the same level of detail the aggregator gets. It never sees raw subagent LLM output. This keeps the phase-9 orchestrator principle intact.
4. **`proven` requires `evidence_url ∈ url_ok_set`.** Without this guard the verdict LLM can and will fabricate a proven URL. Catching this on the orchestrator side is cheaper than asking every LLM to behave.
5. **`url_auth_required` (401/403) is separate from `url_404`.** Bot-gated sites (reddit, twitter) would otherwise get labeled fabricated simply for defending against scrapers. Claims citing them are held to `plausible`, not `fabricated`.
6. **Fail-safe every layer.** Timeout at the orchestrator (15 s), try/except at the orchestrator, try/except in the pipe integration, fallback-to-`unknown` in `_verdict_claims`. Any failure at any step produces a degraded-but-working response; no 500 reaches the user.

## Incidents caught during E2E

1. **Systematic false-positive `fabricated` verdicts (2026-04-17, same-day pivot).** First live run on a Russian news query («что сегодня творится в мире?») produced a stream of ❌ labels on real events: "Военная операция США и Израиля против Ирана продолжается" (реально идёт, перемирие временное), "Силы ПВО России сбили 60 украинских авиабомб и 1665 дронов", "Affordable Art Fair в Германии", "Франсуа Фийон отрицает...". The verdict LLM (`mws/kimi-k2`) interpreted «утверждение не встречается в snippets» as «fabricated» вместо «unknown». Плюс aggregator поверх «использовал метки» — вписывал в ответ словесные комментарии типа «Сообщения о... не подтверждены и отмечены как недостоверные [❌ (3)]», дублируя сигнал текстом. Обратная связь пользователя: «теперь надо проверять на правду ещё и самого "агента правды". Возможно, сама концепция не верна». **Fix (pivot, not patch):** stage перестаёт быть truth-checker'ом. Он становится attribution-checker: «взяло ли утверждение из реально полученного snippet'а, или субагент его выдумал». Новые вердикты: `grounded`/`partial`/`ungrounded`/`unknown`. Метки: ✅ (есть в источнике) / ⚠️ (partial, ungrounded, unknown — всё, что не нашли в source); ❌ удалён. `_VERDICT_PROMPT` полностью переписан и эксплицитно запрещает вывод «fabricated» из отсутствия snippets. Aggregator-инструкция ужесточена: только символ рядом с утверждением, ЗАПРЕЩЕНО словами описывать характер проверки («подтверждено», «не подтверждено», «отмечено как недостоверное»). `fact_check_model` по умолчанию переключён с `mws/kimi-k2` на `mws/gpt-oss-20b` — attribution — более простая задача сравнения строк, чем оценка истинности. Guard `grounded → evidence_url ∈ url_ok_set` сохранён (защита от hallucinated evidence_url), но симметричный guard на `fabricated` не нужен — самой категории больше нет.
2. **`fact_check_model` cost/quality tradeoff re-evaluated.** До пивота `mws/kimi-k2` был выбран ради более умных верификаций истинности. После пивота задача сводится к «появляется ли подстрока/факт в snippet» — `mws/gpt-oss-20b` справляется и стоит дешевле (тот же модель, что и `_extract_claims`). Это заодно уменьшает количество разных upstream-моделей в phase-1.5 с двух до одной.
3. **Все вердикты падали в `⚠️ не определено` — gpt-oss-20b обрезал JSON по `max_tokens` (2026-04-17).** После пивота E2E на запросе «какая погода в Казани» давал 6/6 `unknown`, хотя `yandex.ru/pogoda` и `world-weather.ru` действительно были fetched и содержали факт. Логи внутри `_verdict_claims` / `_extract_claims` показали `JSONDecodeError: Unterminated string starting at: line 1 column 237` — модель доходила до лимита 900 / 600 токенов посреди `"reason": "..."` и молча обрывала вывод. `json.loads` падал → `except` уходил в `verdict_llm_failed` → все claim'ы `unknown`. Маскировалось тем, что старый error-log печатал только `type(e).__name__`, без сырого контента. **Fix:** (a) `max_tokens` поднят до 2500 (verdict) и 1500 (claim-extract); (b) per-URL evidence порезан с 8000 → 4000 символов, чтобы вход не съедал бюджет выхода; (c) новый `_salvage_json_array(content, array_key)` вручную обходит оборванный контент, считает скобки/кавычки, собирает валидный JSON-массив из полностью закрытых объектов — частичные данные лучше полного провала; (d) логи ошибок теперь печатают `content_head=<первые 200 символов>` ответа LLM, чтобы будущий JSON-drift диагностировался по одной строке. Проверено E2E на «погода Казань»: 6/6 `✅ grounded` с правильными `evidence_url`, нулевые ошибки в логах.
4. **web_search citations включали URL, которые субагент не читал.** `_sa_web_search` собирал 3 URL из DuckDuckGo, пытался скачать каждый через `_fetch_url_text`, а при провале откатывался к DDG-сниппету (`body or h['snippet']`) — но URL всё равно попадал в `CompactResult.citations` безусловно. Fact-check потом вытаскивал этот URL из `_dedupe_urls` и снова падал (`url_unreachable`), что пользователь справедливо воспринимал как «web_search соврал, что там был». Не врал: сниппет действительно был из DDG, но URL в citations выглядел как прочитанный источник. **Fix:** `_sa_web_search` и `_sa_deep_research` теперь пишут подмножество citations, у которых body пришёл непустой, в `CompactResult.metadata["fetched_urls"]`. `_dedupe_urls`, если поле присутствует, использует ТОЛЬКО его (не полный citations, не URL, выдранные регэкспом из summary). URL, которые субагент реально не открывал, больше не попадают на attribution-check.
5. **Таймаут валидации URL в fact_check был вдвое меньше, чем у web_search.** `_validate_urls` хардкодил `httpx.Timeout(6.0, connect=3.0)`, а `_fetch_url_text` (web_search) — 15s. Медленные сайты (Cloudflare challenges, геозадержки) стабильно отвечали web_search'у и таймаутили fact_check → тот же URL получал `url_ok` на диспатче и `url_unreachable` на верификации. **Fix:** новый Valve `fact_check_url_timeout: float = 15.0` управляет `_validate_urls`, у обоих этапов единый бюджет. `connect` поднят 3s → 5s.
6. **Привязка вердикта к claim'у была буквально-текстовой — парафраз ломал маппинг.** `_verdict_claims` изначально искал вердикт по точной строке claim-текста, возвращённой LLM. Если gpt-oss-20b перефразировал claim в ответе (добавил точку, изменил регистр, чуть переписал), lookup молча возвращал пусто и все claim'ы уходили в `unknown`. **Fix:** `_VERDICT_PROMPT` теперь требует `{"verdicts":[{"index":N, ...}]}`, где `N` соответствует нумерованному `[1] ... [N]` списку claim'ов в user-сообщении; `_verdict_claims` делает primary-join по `index`. Три fallback'а на drift промпта: (a) по нормализованному тексту (`_norm_claim`: lowercase + убрать пунктуацию + первые 120 символов); (b) позиционное распределение verdicts без `index` и без `claim`; (c) новые debug-логи `fact_check verdict JOIN EMPTY` (verdicts есть, ничего не смёпалось) и `fact_check verdict EMPTY VERDICTS` (LLM вернул `{}` или `{"verdicts":[]}`) чтобы различать режимы провала.
7. **Fallback-модель на «пустой JSON» от primary (2026-04-17).** Отдельная разновидность бага #3: даже с увеличенным `max_tokens=2500` gpt-oss-20b стабильно возвращал литеральный `{}` на новостных запросах вроде «что сегодня творится в мире?». Snippets были не пустые — это были 3 новостные рубрики (`ria.ru/world/`, `iz.ru/rubric/mir`, `euronews.com/news/international`), то есть плотная нехудожественная лента заголовков на ~12 KB. На таком входе gpt-oss-20b в JSON-mode «сдаётся» и отдаёт пустой объект вместо 6 `ungrounded`. Лог: `fact_check verdict EMPTY VERDICTS: raw_data={}`. **Fix:** новый Valve `fact_check_fallback_model = "mws/kimi-k2"` и автоматический retry в `_verdict_claims`, когда primary вернула `{}` или `verdicts: []`. Retry срабатывает ТОЛЬКО на «empty» — не на parse/truncation-ошибки (те уже закрыты `_salvage_json_array`). Логи теперь показывают `fact_check verdict retry: primary=... returned empty, falling back to ... | user_msg_head=...` чтобы был виден и размер входа, и сам факт переключения модели. Плюс `_VERDICT_PROMPT` ужесточён: «КРИТИЧНО: верни вердикт для КАЖДОГО утверждения. Пустой объект {} или пустой список verdicts: [] — это ОШИБКА, так возвращать нельзя ни при каких условиях.» — у модели на одну отмазку меньше. Проверено E2E: «что сегодня творится в мире?» → 6/6 `✅ grounded` с ссылками на `iz.ru/rubric/mir`.
8. **Заголовок details-блока стал динамическим.** Раньше `<summary>` был жёстко `✅ Проверка реальности источников`, даже когда внутри был `url_unreachable` или не-`grounded` вердикт. Пользователь не видел проблему, не раскрыв блок. **Fix:** `_render_fact_check_details` теперь считает эмодзи по содержимому — `✅` только если ВСЕ claim'ы `grounded` И ВСЕ URL живые; иначе `⚠️`. Блок, который пишется при `fc.error` (timeout/exception), уже был `⚠️` — там ничего не меняли.
9. **Lesson для будущих JSON-mode интеграций на gpt-oss-20b.** Модель НЕ самовосстанавливает обрезанный вывод под `response_format={"type":"json_object"}` — она останавливается посреди строки и оставляет клиенту разбираться. Когда вывод — список объектов с `reason` полями на каждый: бюджет надо считать агрессивно (≥400 токенов на элемент × N элементов + 300 на scaffolding), всегда логировать `content_head` при JSON-ошибках, И иметь fallback на более сильную модель для случая «модель сдалась и вернула `{}`». Тот же класс бага задокументирован в `phase-11-done.md#1` для pptx schema LLM — это уже третий раз, когда нас это кусает.
10. **`url_unreachable` на сайте, который web_search только что открыл (раунд 3, 2026-04-17).** После фиксов 4–5 на погодном запросе систематически всплывало: gismeteo.ru попадал и в `fetched_urls` (значит `_fetch_url_text` вернул тело), и в «Недоступные URL» с `url_unreachable`. Причина: `_sa_web_search` делал GET на gismeteo и получал 200, а через ~секунду `_validate_urls` делал второй GET на тот же URL и ловил TCP RST / таймаут — анти-бот сайта режет повторный запрос с того же IP. Unifying таймаутов (#5) не помог: сайт физически не хочет, чтобы к нему ходили дважды за секунду. **Fix:** переиспользуем уже скачанное. `_sa_web_search` и `_sa_deep_research` теперь кладут в `metadata["fetched_bodies"]` словарь `{url: full_page_text}` для всех успешно скачанных URL. `_dedupe_urls` возвращает `(urls, prefetched_bodies)` tuple. `_validate_urls(urls, prefetched=...)` для URL'ов, у которых тело уже есть, **не ходит в сеть** — сразу возвращает `UrlStatus(url_ok, 200, snippet=body[:16384])`. Анти-бот сайты больше физически не могут уйти в `url_unreachable`, если web_search их прочитал. Проверено на «погода Казань»: в details-блоке 6/6 verdict'ов, блок «Недоступные URL» отсутствует.
11. **Таймаут fact_check на новостных запросах — retry на kimi-k2 съедал весь бюджет (раунд 3, 2026-04-17).** На «что сегодня творится в мире?» primary gpt-oss-20b возвращал `{}`, срабатывал retry на `mws/kimi-k2` (фикс #7), и он на плотных новостных сниппетах работал по 20–35 секунд. `fact_check_timeout=30.0` целиком уходил в retry → `asyncio.TimeoutError` на всей фазе → пользователь видел «Проверка не выполнена: timeout» вместо нормального details-блока, теряя даже результат primary. **Fix:** (a) `fact_check_timeout` поднят с 30s → **60s**; (b) retry обёрнут в собственный `asyncio.wait_for(..., timeout=25.0)` — если kimi-k2 не уложился, `_verdict_claims` возвращает то, что есть (или `unknown` на всех claim'ах), вместо того чтобы утянуть всю фазу в timeout; (c) лог `fact_check verdict retry TIMEOUT ({fallback_model}) after 25s` чтобы было видно, кто именно тормозит. Проверено E2E: «что сегодня творится в мире?» → 6/6 `✅ grounded` + заголовок `✅`.

## Limits / not in scope

- **Не truth-checker, а attribution-checker.** Мы не оцениваем, правда ли утверждение в реальности — мы ловим только галлюцинации субагентов. Если источник сам пишет ложь, агент её пропустит с меткой ✅. Это осознанное ограничение после пивота.
- **No Redis cache** of URL validations or claim verdicts. Repeat queries pay full cost every time.
- **No contradiction mining** across multiple sources (if two URLs disagree, only the first snippet that matches is used for `proven`).
- **No image-generation safety review** (phase-12 deliberately excluded `image_gen` from `_CHECKABLE_KINDS`).
- **No PDF/scientific-paper claim tracing** (fact-checker can validate a document URL is alive, but it doesn't open the PDF and match the quote).
- **No interactive "confirm this fact" UI flow** — details block is read-only.
- **No tool-using feedback loop in the aggregator** — the aggregator gets the report as text; it can't ask the fact-checker to re-verify on demand.

## Status

- **Code:** landed in `pipelines/auto_router_function.py`. `python -c "import ast; ast.parse(...)"` passes.
- **Pivot:** truth-check → attribution-check (см. инцидент #1). `fact_check_model` переключён с `mws/kimi-k2` на `mws/gpt-oss-20b`, `fact_check_fallback_model=mws/kimi-k2` как retry. ❌ удалён, метки: `grounded` (✅) / `partial`/`ungrounded`/`unknown` (⚠️).
- **Hermetic E2E (F5/F6):** `scripts/e2e_fact_check_test.py` — зелёные.
- **Live E2E:** зелёные через OpenWebUI chat UI:
  - «что сегодня творится в мире?» → plan `[web_search]`, verifier `fact_check`, 6/6 claims `✅ grounded`, заголовок `✅`.
  - «какая погода в Казани будет в ближайшие 3 дня?» → plan `[web_search]`, 5/6 `✅ grounded` + 1 `⚠️ partial`, заголовок динамически `⚠️`, «Недоступные URL» отсутствует (prefetched bodies reuse).
- **Post-launch fixes:** 11 инцидентов задокументированы выше (пивот + 2 раунда фиксов 2026-04-17).

## Follow-ups (not required for phase-12)

- **Cache layer.** Consider `fact_check:{sha256(url)}` keys in Redis with 24 h TTL for URL validation; `fact_check:verdict:{sha256(claim)}` with 6 h TTL for verdicts. Would amortize cost for repeated queries (e.g., multiple users asking the same trending question).
- **Multi-source cross-check.** When two `url_ok` snippets contain the same factual claim, bump the verdict to `proven` with higher confidence and surface both URLs in `evidence_url` (schema change: `list[str]`).
- **User feedback buttons.** If the user clicks «оспорить» on a ❌ claim, surface it back into the aggregator's next turn as a counter-check request. Requires OpenWebUI frontend changes.
- **PDF/HTML body extraction for doc_qa.** Currently `doc_qa` results don't get their underlying document re-opened by the fact-checker — only URL citations in the summary are validated. A future pass could pull the actual attached PDF/DOCX through the same `pptx-service`-style parsing pipeline to anchor claims in the document.
