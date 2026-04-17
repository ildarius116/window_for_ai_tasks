# phase-12-1 — Архитектурный скелет и Valves

## Цель
Зафиксировать архитектурные решения и добавить в `pipelines/auto_router_function.py` инфраструктуру (valves, заглушки), на которой будут строиться остальные задачи phase-12.

## Что сделать
- В `class Pipe.Valves` добавить поля:
  - `fact_check_enabled: bool = True` — мастер-выключатель.
  - `fact_check_timeout: float = 15.0` — общий deadline для фазы 1.5.
  - `fact_check_max_urls: int = 12`.
  - `fact_check_max_claims: int = 6`.
  - `fact_check_model: str = "mws/kimi-k2"` — модель для вердиктов.
  - `fact_check_claim_model: str = "mws/gpt-oss-20b"` — модель для извлечения claims.
- Добавить в класс `Pipe` пустой метод-заглушку `async def _sa_fact_check(self, results, detected, user_question) -> CompactResult` с `raise NotImplementedError`.
- Добавить приватный метод-заглушку `def _should_fact_check(self, plan, detected) -> bool: return False`.
- Добавить `print`-лог `fact_check: disabled by valves` / `fact_check: nothing to check` для будущих ветвлений — чтобы сразу увидеть, что путь жив.
- Никаких правок `pipe()` и `_stream_aggregate` в этой задаче — только скелет.

## Критерии готовности
- `docker compose restart bootstrap && docker compose restart openwebui` — без ошибок загрузки функции.
- В Admin → Functions пайп «MWS GPT Auto 🎯» всё ещё active, старые сценарии отвечают.
- `Valves` видны в UI OpenWebUI (Settings → Admin → Functions → MWS GPT Auto 🎯 → Valves).

## Затронутые файлы
- `pipelines/auto_router_function.py`
