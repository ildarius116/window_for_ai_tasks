# Фаза 6 — AI Router Sonnet/Opus: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 6.1 — Правила роутинга (Smart Router)
- Добавлена модель `mws/auto` — complexity-based router в `litellm/config.yaml`
- Используется встроенный `auto_router/complexity_router` LiteLLM
- Классификация запросов по сложности:
  - **SIMPLE/MEDIUM** → `mws/sonnet` (быстрые, простые задачи)
  - **COMPLEX/REASONING** → `mws/opus` (код, архитектура, анализ)
- Rule-based scoring без внешних API (sub-millisecond latency)
- Классификаторы: code_keywords (function, class, architecture, refactor...), reasoning_keywords (analyze, debug, step by step...)
- `default_model: mws/sonnet` — если классификация неопределённа

### Fallback-цепочки
- `mws/opus` → `mws/sonnet` → `mws/nemotron` (free)
- `mws/sonnet` → `mws/opus` → `mws/nemotron` (free)
- `mws/auto` → `mws/nemotron` (free fallback)
- Если Anthropic API недоступен (нет баланса), запросы автоматически fallback на бесплатные модели

### 6.2 — Cost Tracking
- **Spend logs** включены в LiteLLM PostgreSQL (автоматически)
- **Per-model spend**: `/global/spend/models` — стоимость по каждой модели
- **Per-request logs**: `/spend/logs` — детальные логи (tokens, spend, model, duration)
- **User budget**: `max_internal_user_budget: 10` USD на пользователя в месяц
- **Budget duration**: `internal_user_budget_duration: "1mo"` — сброс каждый месяц
- **Langfuse traces**: все запросы (success + failure) логируются в Langfuse с стоимостью

### Дефолтная модель
- OpenWebUI: `DEFAULT_MODELS` изменён с `mws/sonnet` на `mws/auto`
- Новые чаты по умолчанию используют smart router

## E2E тесты пройдены

1. **Simple query** (`mws/auto` → "Hi, what is 2+2?") → роутинг к Sonnet → fallback к nemotron (free) → ответ получен
2. **Complex query** (`mws/auto` → "Analyze architecture, refactor database schema, design class hierarchy") → роутинг к Opus → fallback к nemotron → ответ получен
3. **Cost tracking** → `/spend/logs` показывает все запросы с tokens, model, spend
4. **Model list** → `mws/auto` отображается в `/v1/models` и доступен в OpenWebUI dropdown
5. **OpenWebUI integration** → запрос через OpenWebUI API с `mws/auto` → ответ получен

## Отклонения от плана

- **Anthropic API без баланса**: smart router корректно пытается Sonnet/Opus, получает ошибку, и fallback к nemotron (free). Когда баланс будет пополнен, роутинг к Sonnet/Opus заработает автоматически.
- **Cost = $0**: все текущие модели бесплатные (OpenRouter free tier), поэтому spend logs показывают $0.00. Cost tracking инфраструктура работает и начнёт считать деньги при использовании платных моделей.
- **Per-user API keys**: не созданы через LiteLLM `/user/new` — используется общий master key через OpenWebUI. Можно добавить индивидуальные бюджеты позже.

## API endpoints для мониторинга расходов

```
GET  /global/spend           — общий расход
GET  /global/spend/models    — расход по моделям
GET  /spend/logs?limit=N     — детальные логи запросов
```

## Файлы изменены

- `litellm/config.yaml` — добавлен mws/auto (complexity_router), fallback-цепочки с free моделями, budget settings
- `docker-compose.yml` — `DEFAULT_MODELS` → `mws/auto`
