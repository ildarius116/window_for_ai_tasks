# Task: phase-9-10 — Deploy Script + Makefile Target

## Goal
Автоматизировать загрузку Pipe-функции в OpenWebUI через API, чтобы не нужно было копипастить код через Admin UI.

## Context
OpenWebUI предоставляет `POST /api/v1/functions/create` для программной загрузки функций. См. `PLAN_chat_agents.md` раздел 11.

## Scope

### 1. Скрипт `scripts/deploy_function.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail

FILE="${1:?usage: deploy_function.sh <pipelines/*_function.py>}"
: "${OWUI_ADMIN_TOKEN:?OWUI_ADMIN_TOKEN not set}"
: "${OWUI_BASE_URL:=http://localhost:3000}"

# Extract id from filename (e.g. auto_router_function.py -> mws_auto_router)
BASENAME=$(basename "$FILE" _function.py)
ID="mws_${BASENAME}"

# Extract name and description from docstring
NAME=$(grep -oP '(?<=title: )[^\n]+' "$FILE" | head -1 || echo "$ID")
DESC=$(grep -oP '(?<=description: )[^\n]+' "$FILE" | head -1 || echo "MWS GPT function")

# Build JSON payload (jq wraps content safely)
PAYLOAD=$(jq -n \
    --arg id "$ID" \
    --arg name "$NAME" \
    --arg desc "$DESC" \
    --rawfile content "$FILE" \
    '{id:$id, name:$name, meta:{description:$desc}, content:$content}')

# POST
RESPONSE=$(curl -sS -X POST \
    "${OWUI_BASE_URL}/api/v1/functions/create" \
    -H "Authorization: Bearer ${OWUI_ADMIN_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")

echo "Deployed $ID: $RESPONSE"
```

Сделать `chmod +x`.

### 2. Makefile target
```makefile
deploy-functions:
	@bash scripts/deploy_function.sh pipelines/auto_router_function.py
	@bash scripts/deploy_function.sh pipelines/memory_function.py
	@echo "✅ Functions deployed. Enable them in Admin UI → Functions."
```

### 3. Обновить `.env.example`
Добавить:
```
# OpenWebUI admin API key (obtain from Profile → API Keys after admin account creation)
OWUI_ADMIN_TOKEN=
OWUI_BASE_URL=http://localhost:3000
```

### 4. Обновить `CLAUDE.md` и `README_proj.md`
Короткая инструкция: как получить токен и запустить `make deploy-functions`.

## Files
- `scripts/deploy_function.sh` (создать)
- `Makefile` (изменить)
- `.env.example` (изменить)
- `CLAUDE.md` (изменить)
- `README_proj.md` (изменить)

## Acceptance criteria
1. После `make up`, создания admin-аккаунта и `export OWUI_ADMIN_TOKEN=<token>`, команда `make deploy-functions` успешно регистрирует обе функции (HTTP 200 в ответе).
2. В Admin UI OpenWebUI на странице Functions появляются обе функции — `mws_auto_router` и `mws_memory`.
3. Повторный запуск `make deploy-functions` либо обновляет существующие функции, либо возвращает информативную ошибку (не ломает систему).
4. Скрипт работает на Windows (через Git Bash) и на Linux.
5. `.env.example` содержит документацию, как получить токен.

## Dependencies
- phase-9-1 (должна существовать `pipelines/auto_router_function.py`).

## Out of scope
- Обновление существующей функции — если API требует другого метода (PUT/PATCH), это отдельная задача.
- Rotation/refresh токена.
