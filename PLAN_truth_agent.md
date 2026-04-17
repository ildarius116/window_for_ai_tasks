# Phase 12 — Attribution-Checker Subagent («Truth Agent»)

> **Pivot note (2026-04-17):** первоначальный дизайн (ниже) пытался
> оценивать истинность утверждений (`proven`/`plausible`/`fabricated`).
> Живой E2E показал систематические ложноположительные `fabricated` для
> реальных новостей (источники не всегда попадали в snippets, а verdict
> LLM интерпретировал «нет в snippets» как «ложь»). После обсуждения
> («теперь надо проверять на правду и самого агента правды — возможно,
> сама концепция не верна») концепция изменена: агент больше **не
> проверяет истинность**. Он проверяет только **attribution** — взял ли
> субагент утверждение из реально полученного snippet или выдумал.
> Новые вердикты: `grounded` (✅ есть в источнике) / `partial` (⚠️
> тема в источнике, детали нет) / `ungrounded` (⚠️ в источниках нет —
> возможная галлюцинация субагента) / `unknown` (⚠️ verdict LLM не
> смог решить). Категория ❌ удалена целиком: «нет пруфа ≠ ложь».
> Агрегатору запрещено словами описывать результат проверки — только
> символ метки. Остальной текст плана сохранён как исторический контекст.


## Context

Сейчас `pipelines/auto_router_function.py` содержит 14 субагентов. Ни один
из них не проверяет реальность фактов и ссылок в собственных ответах.
Наблюдаемая проблема (запрос «Мисс Мира 2025»): субагенты `image_gen` +
`deep_research` вернули ответ с двумя ссылками `[1] wikipedia.org…` и
`[2] rbc.ru…`. Пользователь не знает, существуют ли эти URL на самом деле,
подтверждён ли факт победы «Opal Suchata Chuangsri», не выдуман ли
«проект Opal For Her». Аггрегатор (`qwen3-235b` / `gpt-alpha`) работает
только с компактами и не имеет инструмента для валидации.

В `.claude/agents.json` был добавлен dev-time `FactCheckerAgent` — но он
живёт в каталоге разработческих субагентов Claude Code и к рантайму
OpenWebUI-пайпа отношения не имеет (см. переписку 2026-04-17). Поэтому
фактчекер в продакшен-пайплайне **физически отсутствует** и в
routing decision не появляется.

Цель Phase 12 — добавить рантайм-сабагент `_sa_fact_check`, который
запускается **после** основной фазы `asyncio.gather` и **до**
`_stream_aggregate`, проверяет ссылки и ключевые утверждения других
субагентов, и пробрасывает в аггрегатор структурированный
`FactCheckReport` с тремя категориями меток:

- **(1) проверено** — URL отвечает 2xx и совпадение содержимого
  подтверждено / утверждение подтверждено независимым источником;
- **(2) правдоподобно** — поверхностные признаки в порядке (домен жив,
  формулировка не противоречит базовым фактам), но независимого
  подтверждения не нашли;
- **(3) выдумано** — URL не резолвится/404/5xx, или утверждение
  противоречит результату поиска / извлечённого контента.

Аггрегатор обязан использовать эти метки в финальном ответе и **не
выдавать** утверждения уровня (3) как факт.


## Архитектурное решение

**Выбранный вариант**: двухфазная оркестрация внутри существующего `pipe()`.

```
  phase 1 (existing):      asyncio.gather(_run_subagent × plan)
       ↓  list[CompactResult]
  phase 1.5 (new):         if _should_fact_check(plan, results):
                              fc = await _sa_fact_check(results, detected)
                              results.append(fc)   # kind="fact_check"
       ↓  list[CompactResult] (len+1)
  phase 2 (existing):      async for chunk in _stream_aggregate(...):
                              yield chunk
```

Почему после, а не параллельно: фактчекер валидирует **выход** других
субагентов (их URL и утверждения), а значит должен иметь их на руках.
Параллельный запуск с `_run_subagent` дал бы ему на вход только запрос
пользователя, а не результаты — тогда это был бы ещё один
`web_search`, а не проверка.

Почему не как tool/feedback loop внутри аггрегатора: это потребовало бы
tool-calling, разрушение потокового вывода и лишний round-trip. Фаза 1.5
проще и предсказуемее.

**Инвариант контекст-изоляции сохраняется**: фактчекер получает только
`CompactResult.summary` (≤500 токенов) и `CompactResult.citations` от
предыдущих субагентов — ровно тот же уровень детализации, что получает
аггрегатор. Он не видит сырых ответов LLM от других субагентов.

### Когда запускается

Активация — по **типу** плана, не по интенту пользователя. Если в `plan`
есть хотя бы один субагент из `_CHECKABLE_KINDS`, добавляем fact_check:

```python
_CHECKABLE_KINDS = {
    "web_search",       # DuckDuckGo + kimi-k2 — высокий риск галлюцинации
    "web_fetch",        # URL из сообщения — валидировать их доступность
    "deep_research",    # многошаговое исследование, много утверждений
    "memory_recall",    # факты из БД — проверить, что они актуальны
    "doc_qa",           # ответы по документу — могут выйти за пределы
}
```

Для `general`, `ru_chat`, `code`, `reasoner`, `long_doc`, `vision`, `stt`,
`image_gen`, `presentation` fact-check **не** запускается — там либо нет
утверждений о реальном мире (код, изображения), либо верификация несоизмерима
по стоимости с ценностью (general-чат). Список можно расширить позже
через valve `fact_check_kinds`.

Плюс user-override: если последнее сообщение содержит триггерные слова
("проверь факты", "verify", "fact-check"), fact_check активируется
принудительно даже для general.

### Что проверяет

Для каждого `CompactResult` из списка `checkable`:

1. **URL-валидация**: из `result.citations` и `_URL_RE.findall(result.summary)`
   собираются все URL. Для каждого — async HTTP HEAD (timeout 5s), при
   3xx/405 — GET с `stream=True` на первые 4 KB. Статусы:
   - 2xx → `url_ok`
   - 3xx → `url_redirect` (записываем finalUrl)
   - 4xx → `url_404` (важнейший красный флаг — выдуманная ссылка)
   - 5xx / timeout / DNS fail → `url_unreachable` (отдельная метка —
     не галлюцинация, но и не подтверждение)
   Параллельно — `asyncio.gather` с ограничением `Semaphore(8)`.
2. **Извлечение утверждений**: LLM-шаг (`mws/gpt-oss-20b`, JSON mode,
   `max_tokens=600`) выделяет из `summary` до 6 «проверяемых»
   утверждений: факты про людей/события/даты/числа/цитаты. Игнорирует
   общие фразы ("это интересная тема").
3. **Second-opinion по утверждениям**: один агрегированный LLM-вызов
   (`mws/kimi-k2`, `max_tokens=800`) с JSON-output, которому передаются:
   - оригинальный вопрос пользователя,
   - список claims,
   - контент URL-проверок (finalUrl, первые 2 KB текста — если url_ok).
   Модель выдаёт вердикт `{claim, verdict: proven|plausible|fabricated, evidence_url?}`
   для каждого утверждения.
4. **Сводный отчёт** в `CompactResult(kind="fact_check", summary=…, metadata={"report": FactCheckReport})`.

### Как используется аггрегатором

`_stream_aggregate` получит новый параметр `fact_check: Optional[FactCheckReport]`.
Если отчёт есть, в system-prompt аггрегатора добавляется блок:

```
--- FACT-CHECK REPORT ---
URLs checked: 2/2 ok, 0 broken.
Claims:
  ✅ (1) «Opal Suchata Chuangsri won Miss World 2025» — proven via https://…wikipedia
  ⚠️ (2) «Церемония прошла в Хайдарабаде» — plausible, no independent source
  ❌ (3) «Корону вручила Кристина Пышкова из Чехии» — fabricated, no match in evidence
---
Используй эти метки в ответе. Не повторяй утверждения с меткой (3).
Для (2) явно пиши «по некоторым источникам». Для (1) можешь цитировать.
```

Для UI: в конец финального ответа добавляется свёрнутый
`<details>✅ Проверка источников</details>` блок, собираемый из отчёта.
Это — дополнительный вывод, не заменяющий основной текст.


## Компоненты и правки

### 1. Новые dataclasses (`auto_router_function.py`)

```python
@dataclass
class Claim:
    text: str
    source_kind: str          # от какого субагента пришло
    verdict: str = "unknown"  # proven | plausible | fabricated | unknown
    evidence_url: Optional[str] = None
    reason: str = ""

@dataclass
class UrlStatus:
    url: str
    status: str               # url_ok | url_redirect | url_404 | url_unreachable
    http_code: Optional[int] = None
    final_url: Optional[str] = None
    snippet: str = ""         # первые ~2KB при url_ok, для передачи в LLM

@dataclass
class FactCheckReport:
    urls: list[UrlStatus] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    total_checked_kinds: list[str] = field(default_factory=list)
    error: Optional[str] = None
```

### 2. Помощники

- `_collect_urls(results) -> list[str]` — дедуплицированный список URL
  из `result.citations` + `_URL_RE.findall(result.summary)` для всех
  чекабл-результатов.
- `_validate_urls(urls) -> list[UrlStatus]` — async gather c semaphore.
  Исключает `localhost`, приватные диапазоны (защита от SSRF через
  галлюцинацию URL).
- `_extract_claims(results, user_question) -> list[Claim]` — LLM-вызов.
- `_verdict_claims(claims, url_statuses, user_question) -> list[Claim]` —
  LLM-вызов с доказательствами.

### 3. Новый субагент

- `_sa_fact_check(results, detected, user_question) -> CompactResult` —
  оркестрирует все 4 шага, возвращает `CompactResult(kind="fact_check")`.
- **Важно**: этот метод не обёрнут в `_run_subagent`, потому что тот
  строит `SubTask` с `input_text` — а фактчекер хочет `list[CompactResult]`.
  Вызывается напрямую из `pipe()`.

### 4. Правки `pipe()`

- После строки `results = await self._dispatch(plan, trace_id=trace_id)`:
  ```python
  results = await self._maybe_reclassify_stt(...)
  if self._should_fact_check(plan, detected):
      try:
          fc = await self._sa_fact_check(results, detected, user_question)
          results.append(fc)
      except Exception as e:
          # Ни при каких условиях не валим основной ответ
          print(f"fact_check FAILED: {type(e).__name__}: {e}")
  ```
- `_should_fact_check(plan, detected) -> bool`:
  ```python
  kinds = {t.kind for t in plan}
  return bool(kinds & _CHECKABLE_KINDS) or _has_fact_check_trigger(detected.last_user_text)
  ```

### 5. Правки `_stream_aggregate`

- Достаёт `fc = next((r for r in results if r.kind == "fact_check"), None)`.
- Если есть — строит текстовый блок и добавляет в system-prompt.
- Из `results`, передаваемых в сборку контекста, `fc` **исключается**
  (иначе модель процитирует отчёт как собственный ответ).
- После генерации основного ответа (перед `yield` последнего фрагмента)
  дописывает `<details>✅ Проверка источников</details>` блок из
  `fc.metadata["report"]`.

### 6. Classifier — без правок

Фактчекер запускается **автоматически** по составу плана, а не по
отдельной интенции от LLM-классификатора. Это снижает риск, что
классификатор пропустит активацию (как было с `memory_recall`).
Исключение — явный триггер в тексте, обрабатывается отдельной regex
`_FACT_CHECK_TRIGGER_RE`.

### 7. Valves

Добавить в `class Pipe.Valves`:

- `fact_check_enabled: bool = True` — мастер-выключатель.
- `fact_check_timeout: float = 15.0` — общий deadline для фазы 1.5.
- `fact_check_max_urls: int = 12` — защита от деки с 50 ссылками.
- `fact_check_max_claims: int = 6`.
- `fact_check_model: str = "mws/kimi-k2"` — для вердиктов.


## Риски и митигации

| Риск | Митигация |
|---|---|
| Фаза 1.5 увеличивает latency на 3–6 с | async HTTP-gather + agressive timeouts, общий deadline 15s, fallback — отчёт без вердиктов (только URL-статусы) |
| LLM-вердиктор сам галлюцинирует | `evidence_url` обязательно ссылается на один из `url_ok` URL; если нет — verdict форсируется в `plausible` |
| SSRF через «проверку» URL из ответа LLM | чёрный список: `localhost`, `127.0.0.0/8`, `10/8`, `172.16/12`, `192.168/16`, `169.254/16`; используем httpx с `follow_redirects=False` и ручной обработкой 3xx |
| Стоимость (3 доп. LLM-вызова на каждый web-запрос) | активация только для `_CHECKABLE_KINDS`, `gpt-oss-20b` — дешёвая модель для claim-extraction |
| Конфликт с существующим пост-стрим strip (`_FILE_LINK_RE`) | `<details>` блок добавляется **после** отработки strip в `_stream_aggregate`, не попадает под регексы |
| Ложноположительный `url_404` из-за 403-бот-защиты (reddit/twitter) | считать 401/403 отдельной меткой `url_auth_required` и не помечать такие утверждения как fabricated |


## Не входит в Phase 12 (вынесено на потом)

- Кеш верификаций (Redis) — повторная проверка одинаковых URL.
- Поиск противоречий между несколькими источниками.
- Проверка цитат из научных статей / PDF (потребовало бы PDF-парсер в фактчекере).
- Интерактивный UI-флоу «подтвердите факт» с кнопками.
- Проверка результатов `image_gen` (безопасность сгенерированного контента).


## Задачи

- `phase-12-1-architecture-and-valves.md` — финализация архитектурных развилок, добавление `Valves` и заглушек классов.
- `phase-12-2-fact-check-schemas.md` — dataclasses `Claim`, `UrlStatus`, `FactCheckReport`; константы `_CHECKABLE_KINDS`, `_FACT_CHECK_TRIGGER_RE`.
- `phase-12-3-url-validator.md` — `_validate_urls` + SSRF-защита + тесты на `httpbin`.
- `phase-12-4-claim-extractor.md` — `_extract_claims` (LLM), JSON-schema, anti-hallucination prompt.
- `phase-12-5-verdict-llm.md` — `_verdict_claims` (LLM), вердикты `proven/plausible/fabricated`.
- `phase-12-6-sa-fact-check.md` — `_sa_fact_check` оркестратор всех четырёх шагов.
- `phase-12-7-pipe-two-phase-integration.md` — правки `pipe()`, вызов фазы 1.5, taming исключений.
- `phase-12-8-aggregator-report-injection.md` — правки `_stream_aggregate` (system-prompt блок + details-блок в хвосте).
- `phase-12-9-e2e-verification.md` — smoke: «Мисс Мира 2025», «100-метровка рекорд 2024», невалидный URL, принудительный триггер.
- `phase-12-10-docs-claude-md.md` — обновить `CLAUDE.md` (раздел «Auto-Router»), `.env.example`, написать `tasks_done/phase-12-done.md`.
