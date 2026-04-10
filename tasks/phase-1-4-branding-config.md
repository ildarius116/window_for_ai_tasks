# Phase 1.4 — MWS брендинг и конфиг OpenWebUI

**Агент:** OpenWebUIAgent
**Зависимости:** 1.3
**Статус:** TODO

## Задача

Настроить OpenWebUI под MWS: название, модели, системный промпт.

## Конфигурация через ENV vars (в docker-compose)

```yaml
environment:
  WEBUI_NAME: "MWS GPT"
  DEFAULT_MODELS: "mws/sonnet"
  DEFAULT_PROMPT_SUGGESTIONS: ""
  ENABLE_SIGNUP: "true"
  ENABLE_LOGIN_FORM: "true"
  WEBUI_AUTH: "true"
```

## Описания моделей

В OpenWebUI admin panel (или через API `/api/models`) задать:
- `mws/sonnet` → "Claude Sonnet 4.6 — быстрый, общие задачи и код"
- `mws/opus` → "Claude Opus 4.6 — мощный, сложный анализ и длинные документы"

## Системный промпт по умолчанию

```
Ты — MWS GPT, AI-ассистент. Отвечай на языке пользователя (по умолчанию — русский).
Будь краток и точен. При написании кода всегда указывай язык.
```

## Критерии готовности

- [ ] Название "MWS GPT" отображается в заголовке браузера и шапке
- [ ] При первом входе доступны обе модели: mws/sonnet и mws/opus
- [ ] Чат с Sonnet работает (ответ приходит)
- [ ] Чат с Opus работает (ответ приходит)
