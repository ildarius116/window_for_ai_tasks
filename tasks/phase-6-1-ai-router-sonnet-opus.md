# Phase 6.1 — AI Router: умный выбор Sonnet/Opus

**Агент:** AIRouterAgent
**Зависимости:** 1.2
**Статус:** TODO

## Задача

Реализовать правила авто-выбора между Sonnet и Opus на базе LiteLLM router.

## Стратегия роутинга

| Задача | Модель | Признаки |
|--------|--------|----------|
| Общий чат, объяснения, перевод | Sonnet | короткие запросы, нет кода |
| Сложный код, архитектура | Opus | > 500 токенов ИЛИ ключевые слова: refactor, architecture, design |
| Анализ длинных документов | Opus | > 3000 токенов в контексте |
| Быстрые ответы / автодополнение | Sonnet | < 100 токенов |

## Реализация

### Вариант A: LiteLLM router rules (простой)

```yaml
router_settings:
  routing_strategy: usage-based-routing-v2
  model_group_alias:
    auto:
      - mws/sonnet  # primary
      - mws/opus    # fallback для сложных задач
```

### Вариант B: OpenWebUI Pipeline (умный)

```python
class RouterPipeline:
    def inlet(self, body, user):
        last_msg = body["messages"][-1]["content"]
        total_tokens = estimate_tokens(body["messages"])

        if total_tokens > 3000 or is_complex_task(last_msg):
            body["model"] = "mws/opus"
        else:
            body["model"] = "mws/sonnet"

        return body
```

Начать с Варианта B — более гибкий.

## Cost Tracking

В LiteLLM включить `spend_logs`:
```yaml
litellm_settings:
  store_model_in_db: true
```

Лимиты на пользователя через LiteLLM `/user/new` API.

## Критерии готовности

- [ ] Pipeline определяет модель по сложности запроса
- [ ] Простые запросы идут на Sonnet
- [ ] Сложные (много токенов / ключевые слова) идут на Opus
- [ ] В Langfuse видно какая модель использовалась
