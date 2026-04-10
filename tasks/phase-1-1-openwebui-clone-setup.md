# Phase 1.1 — Клонирование OpenWebUI

**Агент:** DevOpsAgent
**Зависимости:** нет
**Статус:** TODO

## Задача

Получить снапшот OpenWebUI v0.8.12 и разместить в репозитории.

## Шаги

1. `git clone --depth 1 --branch v0.8.12 https://github.com/open-webui/open-webui.git openwebui/`
2. Удалить `.git/` внутри `openwebui/` → это снапшот, не форк
3. Добавить в корневой `.gitignore`:
   - `openwebui/node_modules/`
   - `openwebui/.svelte-kit/`
   - `openwebui/build/`
4. Проверить структуру: должны быть `src/`, `backend/`, `package.json`
5. Прочитать `openwebui/README.md` и `openwebui/docker-compose.yaml` — зафиксировать, что изменилось vs v0.5

## Критерии готовности

- [ ] `openwebui/` существует в репо
- [ ] Внутри нет `.git/`
- [ ] `openwebui/package.json` содержит `"open-webui"` как name
- [ ] `openwebui/backend/` содержит Python backend
