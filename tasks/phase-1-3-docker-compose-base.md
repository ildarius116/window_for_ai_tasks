# Phase 1.3 — docker-compose базовый стек

**Агент:** DevOpsAgent
**Зависимости:** 1.1, 1.2
**Статус:** TODO

## Задача

Собрать `docker-compose.yml` с базовым стеком и `.env.example`.

## Сервисы

| Сервис | Image | Порт |
|--------|-------|------|
| postgres | postgres:16-alpine | 5432 |
| redis | redis:7-alpine | 6379 |
| litellm | (из litellm/Dockerfile) | 4000 |
| openwebui | (из openwebui/) | 3000 |
| nginx | nginx:alpine | 80, 443 |

## Требования

- OpenWebUI должен получать `OPENAI_API_BASE_URL=http://litellm:4000/v1` и `OPENAI_API_KEY=${LITELLM_MASTER_KEY}`
- OpenWebUI подключается к `postgres` с отдельной БД `openwebui`
- Все сервисы в сети `mws-network` (bridge)
- Health checks: postgres (`pg_isready`), redis (`redis-cli ping`), litellm (`/health`), openwebui (`/health`)
- Volumes: postgres_data, redis_data, openwebui_data

## `.env.example` переменные

```
ANTHROPIC_API_KEY=
LITELLM_MASTER_KEY=   # generate: openssl rand -hex 32
OPENWEBUI_SECRET_KEY= # generate: openssl rand -hex 32
POSTGRES_PASSWORD=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=http://langfuse:3000
```

## Критерии готовности

- [ ] `docker compose up` поднимает все 5 сервисов без ошибок
- [ ] OpenWebUI доступен на http://localhost:3000
- [ ] LiteLLM доступен на http://localhost:4000
- [ ] Health checks все зелёные: `docker compose ps`
