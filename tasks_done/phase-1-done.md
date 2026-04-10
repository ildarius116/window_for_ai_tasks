# Фаза 1 — OpenWebUI + LiteLLM: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 1.1 — OpenWebUI
- Вместо сборки из исходников (snapshot v0.8.12) использован **prebuilt image** `ghcr.io/open-webui/open-webui:main` — решение принято из-за нестабильной сети (apt-get таймауты, Cypress download fail, JS heap OOM при npm build).
- Исходники OpenWebUI остаются в `openwebui/` с патчами в Dockerfile (`CYPRESS_INSTALL_BINARY=0`, `NODE_OPTIONS=--max-old-space-size=4096`), но не используются.

### 1.2 — LiteLLM + модели
- `litellm/config.yaml` настроен с **3 провайдерами, 7 моделями**:
  - Anthropic: `mws/sonnet` (claude-sonnet-4-6), `mws/opus` (claude-opus-4-6) — требуют баланса
  - OpenRouter free: `mws/nemotron` (120B), `mws/nemotron-nano` (9B), `mws/qwen-coder`, `mws/qwen-or`
  - DashScope: `mws/qwen` (qwen-plus)
- Fallbacks: opus ↔ sonnet
- Healthcheck изменён с curl на python urllib (curl отсутствует в контейнере LiteLLM)

### 1.3 — docker-compose
- 5 базовых сервисов: postgres, redis, litellm, openwebui, nginx
- Healthchecks на всех сервисах
- `.env.example` с переменными для всех провайдеров
- `OPENROUTER_API_KEY` маппится из `OPENAI_API_KEY` в docker-compose.yml

### 1.4 — Брендинг
- `WEBUI_NAME: "MWS GPT"`
- `DEFAULT_MODELS: "mws/sonnet"`
- `ENABLE_OLLAMA_API: "false"`

## Проверенные модели

| Модель | Статус |
|--------|--------|
| mws/nemotron | Работает |
| mws/nemotron-nano | Работает |
| mws/qwen-coder | Не проверена |
| mws/qwen-or | Rate limit (временно) |
| mws/qwen | Работает (после обновления ключа) |
| mws/sonnet | Нет баланса Anthropic |
| mws/opus | Нет баланса Anthropic |

## Отклонения от плана

- OpenWebUI: prebuilt image вместо сборки из snapshot — сеть не позволяла стабильно собрать
- Добавлены OpenRouter и DashScope провайдеры (не было в исходном плане) — нужны рабочие модели при нулевом балансе Anthropic
