# Phase 7.1 — Svelte UI Кастомизация

**Агент:** FrontendAgent
**Зависимости:** 3.4, 4.1
**Статус:** TODO

## Задача

MWS-специфичные изменения в SvelteKit OpenWebUI фронтенде.

## Брендинг (минимальный)

Через env vars (не требует изменения кода):
- `WEBUI_NAME=MWS GPT`
- `WEBUI_FAVICON_URL=/static/mws-favicon.png`

Если нужен кастомный лого — заменить `openwebui/static/favicon.png`.

## Кастомная CSS тема

Создать `openwebui/static/custom.css`:
```css
:root {
  --color-primary: #2563eb;       /* MWS blue */
  --color-primary-dark: #1d4ed8;
}
```

OpenWebUI поддерживает `CUSTOM_CSS_URL` env var для подключения.

## Панель воспоминаний (Memory UI)

Добавить в боковую панель OpenWebUI компонент для:
- Просмотра списка воспоминаний текущего пользователя
- Удаления конкретного воспоминания
- Кнопка вызова API Memory Service

Файлы для изменения в OpenWebUI Svelte:
- `openwebui/src/lib/components/sidebar/` — добавить MemoryPanel.svelte

## Лейблы моделей

В `openwebui/src/lib/i18n/` или через API — добавить описания:
- `mws/sonnet` → "Sonnet 4.6 ⚡ Быстрый"
- `mws/opus` → "Opus 4.6 🧠 Мощный"

## Критерии готовности

- [ ] Название "MWS GPT" и кастомные цвета в UI
- [ ] Модели отображаются с описаниями
- [ ] Панель воспоминаний доступна из боковой панели
