# Task: phase-9-4 — SubTask / CompactResult Interface + Dispatcher

## Goal
Определить общий интерфейс для всех субагентов и сделать dispatcher на `asyncio.gather`, плюс общий helper для вызовов LiteLLM, которым будут пользоваться все субагенты.

## Context
Ключевое инвариантное свойство роутера: каждый субагент возвращает компактный `CompactResult` (summary ≤ 500 токенов), чтобы оркестратор не раздувал свой контекст. См. `PLAN_chat_agents.md` разделы 3 и 6.

## Scope
- Dataclasses:
  ```python
  @dataclass
  class SubTask:
      kind: str
      input_text: str
      attachments: list[dict] = field(default_factory=list)
      model: str = ""
      max_output_tokens: int = 400
      metadata: dict = field(default_factory=dict)

  @dataclass
  class CompactResult:
      kind: str
      summary: str
      citations: list[str] = field(default_factory=list)
      artifacts: list[dict] = field(default_factory=list)
      error: str | None = None
  ```
- Метод `async def _call_litellm(self, model: str, messages: list, **kwargs) -> dict`:
  - POST `{base_url}/chat/completions`
  - Заголовок `Authorization: Bearer {api_key}`.
  - `kwargs` пробрасывается в body (`temperature`, `max_tokens`, `response_format`, `stream=False`).
  - Timeout 60s, на ошибку — raise.
- Метод `async def _run_subagent(self, task: SubTask) -> CompactResult`:
  - Dispatch через `match task.kind`.
  - Каждый case вызывает одноимённую функцию `self._sa_<kind>(task)`.
  - На `Exception` возвращает `CompactResult(kind=task.kind, summary="", error=str(e))`.
- Метод `async def _dispatch(self, plan: list[SubTask]) -> list[CompactResult]`:
  - `results = await asyncio.gather(*[self._run_subagent(t) for t in plan], return_exceptions=False)`
  - Фильтрация error'ов через `error is not None` — логируем в debug, но не падаем.
- Stub-реализации `_sa_<kind>` для всех 13 видов — возвращают `CompactResult(kind, summary=f"[stub for {kind}]")`. Реальные реализации — в phase-9-5..9-8, 9-11.

## Files
- `pipelines/auto_router_function.py` (изменить)

## Acceptance criteria
1. `asyncio.gather` вызывается с `return_exceptions=False`, но `_run_subagent` оборачивает любое исключение в `CompactResult(error=...)`.
2. `_call_litellm` использует `httpx.AsyncClient` или `aiohttp.ClientSession`, не `requests`.
3. При вызове pipe() с plan из 2 SubTask'ов stub'ы возвращают 2 `CompactResult`'а с корректными `kind`.
4. `CompactResult` с `error` не блокирует другие субагенты в том же `gather`.
5. Все subagent-функции существуют (хоть stub'ами): `_sa_general`, `_sa_ru_chat`, `_sa_code`, `_sa_reasoner`, `_sa_long_doc`, `_sa_vision`, `_sa_stt`, `_sa_image_gen`, `_sa_web_fetch`, `_sa_web_search`, `_sa_doc_qa`, `_sa_deep_research`, `_sa_presentation`.

## Dependencies
- phase-9-1 (каркас).

## Out of scope
- Реальные реализации субагентов — phase-9-5, 9-6, 9-7, 9-8, 9-11.
- Агрегация — phase-9-9.
