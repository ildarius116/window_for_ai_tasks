# Phase 1.2 — LiteLLM: конфиг Anthropic (Sonnet + Opus)

**Агент:** AIRouterAgent
**Зависимости:** нет
**Статус:** TODO

## Задача

Настроить LiteLLM как AI Gateway с двумя моделями Anthropic.

## Файлы для создания

### `litellm/config.yaml`

```yaml
model_list:
  - model_name: mws/sonnet
    litellm_params:
      model: claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY
  - model_name: mws/opus
    litellm_params:
      model: claude-opus-4-6
      api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  routing_strategy: simple-shuffle
  fallbacks:
    - {"mws/opus": ["mws/sonnet"]}

litellm_settings:
  success_callback: ["langfuse"]
  failure_callback: ["langfuse"]
  langfuse_public_key: os.environ/LANGFUSE_PUBLIC_KEY
  langfuse_secret_key: os.environ/LANGFUSE_SECRET_KEY
  langfuse_host: os.environ/LANGFUSE_HOST

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
```

### `litellm/Dockerfile`

Простой образ на основе `ghcr.io/berriai/litellm:main-latest`.

## Критерии готовности

- [ ] `litellm/config.yaml` создан
- [ ] `litellm/Dockerfile` создан
- [ ] Оба алиаса (`mws/sonnet`, `mws/opus`) работают через `curl /health`
- [ ] Langfuse callback настроен (не обязательно работает до Фазы 2)
