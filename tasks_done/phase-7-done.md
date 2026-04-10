# Фаза 7 — UI Customization: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-29
**Статус:** DONE

## Что сделано

### 7.1 — Брендинг и визуал
- **WEBUI_NAME**: "MWS GPT" (установлен с фазы 1)
- **Welcome Banner**: информационный баннер о возможностях платформы (smart routing, long-term memory, voice input)
- **Custom CSS Theme**: файл `openwebui/static/custom.css` монтируется в контейнер
  - Accent цвет: indigo (#6366f1)
  - Стилизация chat input, sidebar, banner gradient, scrollbar
  - Монтируется в два пути: `/app/backend/open_webui/static/custom.css` + `/app/build/static/custom.css`

### 7.2 — Кастомные модели с описаниями
8 моделей с человекочитаемыми именами и описаниями:

| ID | Название | Описание |
|----|----------|----------|
| mws-auto | MWS Auto Smart Router | Авто-маршрутизация Sonnet/Opus + fallback |
| mws-sonnet | MWS Sonnet | Anthropic Claude Sonnet — быстрая модель |
| mws-opus | MWS Opus | Anthropic Claude Opus — сложный анализ |
| mws-nemotron | MWS Nemotron 120B (free) | NVIDIA 120B через OpenRouter |
| mws-nemotron-nano | MWS Nemotron Nano (free) | NVIDIA 9B — лёгкая и быстрая |
| mws-qwen-coder | MWS Qwen Coder (free) | Qwen 3 Coder — специализация на коде |
| mws-qwen-or | MWS Qwen 80B (free) | Qwen 3 Next 80B через OpenRouter |
| mws-qwen | MWS Qwen Plus | Qwen Plus через DashScope |

### 7.3 — Инструменты (Tools)
Два OpenWebUI Tool, доступных из чата (привязаны к модели mws-auto):

**MWS Memory Manager** (`mws_memory_tool`):
- `list_memories` — показать все сохранённые воспоминания
- `search_memories` — семантический поиск по памяти
- `delete_memory` — удалить конкретное воспоминание
- `clear_all_memories` — очистить всю память пользователя

**MWS Usage Stats** (`mws_usage_stats`):
- `get_usage_stats` — статистика расходов по моделям
- `get_recent_requests` — последние запросы с токенами и стоимостью
- Valve: `LITELLM_API_KEY` настроен для доступа к LiteLLM API

### 7.4 — Системный промпт
- mws-auto модель имеет кастомный system prompt, описывающий возможности (memory, usage stats)
- Инструменты привязаны к модели через `toolIds`

## Верификация

Все компоненты подтверждены через API:
1. **8 кастомных моделей** — отображаются в `/api/v1/models/list`
2. **2 инструмента** — развёрнуты через `/api/v1/tools/`
3. **1 фильтр** — `mws_memory` (global, active) через `/api/v1/functions/`
4. **Баннер** — отображается через `/api/v1/configs/banners`
5. **Custom CSS** — статус 200 на `/static/custom.css`
6. **Всё сохраняется** после `docker compose up -d` (данные в PostgreSQL + CSS монтирован через volume)

## Файлы создано/изменено

- `openwebui/static/custom.css` — MWS тема (volume-mounted)
- `pipelines/memory_tool.py` — OpenWebUI Tool для управления памятью
- `pipelines/usage_stats_tool.py` — OpenWebUI Tool для статистики использования
- `docker-compose.yml` — добавлен mount для custom.css
