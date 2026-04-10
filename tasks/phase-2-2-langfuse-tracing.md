# Phase 2.2 — Langfuse LLM Tracing

**Агент:** DevOpsAgent
**Зависимости:** 2.1
**Статус:** TODO

## Задача

Self-hosted Langfuse для трейсинга LLM запросов (модель, токены, стоимость, latency).

## Сервис

```yaml
langfuse:
  image: langfuse/langfuse:latest
  ports:
    - "3001:3000"
  environment:
    DATABASE_URL: postgresql://...
    NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET}
    NEXTAUTH_URL: http://localhost:3001
    SALT: ${LANGFUSE_SALT}
```

Langfuse требует отдельную БД (можно отдельную базу в том же postgres).

## Интеграция с LiteLLM

В `litellm/config.yaml` уже настроены callbacks. После запуска Langfuse нужно:
1. Создать проект в Langfuse UI (http://localhost:3001)
2. Получить `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY`
3. Добавить в `.env`

## Критерии готовности

- [ ] Langfuse на :3001 запускается
- [ ] LiteLLM шлёт трейсы в Langfuse
- [ ] Трейс содержит: модель, prompt tokens, completion tokens, latency
