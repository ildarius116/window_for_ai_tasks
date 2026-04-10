# MWS GPT Platform — Plan (OpenWebUI-based)

## Концепция

Настоящий снапшот OpenWebUI v0.8.12 как основа + кастомные компаньон-сервисы поверх.
Две рабочие модели: `claude-sonnet-4-6` и `claude-opus-4-6` через LiteLLM.

## Архитектура

```
Nginx (80/443)
├── OpenWebUI (3000)         ← SvelteKit UI + built-in RAG/Voice/Auth
│   └── LiteLLM (4000)      ← роутинг к Sonnet / Opus
├── Memory Service (8001)    ← FastAPI microservice: pgvector память
├── Langfuse (3001)          ← LLM tracing
├── Prometheus (9090)        ← метрики
└── Grafana (3002)           ← дашборды
```

Базы данных: PostgreSQL (OpenWebUI) + pgvector (Memory Service) + Redis (cache)

---

## Фаза 1 — OpenWebUI Snapshot + LiteLLM (дни 1–3)

**Цель:** Рабочий чат с Sonnet и Opus.

### 1.1 — Клонирование OpenWebUI
- Скачать снапшот OpenWebUI v0.8.12 (git clone --depth 1, удалить .git)
- Разместить в корне репозитория как `openwebui/`
- Зафиксировать структуру в .gitignore

### 1.2 — LiteLLM: конфиг Anthropic
- Создать `litellm/config.yaml` с моделями:
  - `mws/sonnet` → `claude-sonnet-4-6` (Anthropic)
  - `mws/opus` → `claude-opus-4-6` (Anthropic)
- Настроить rate limits, fallbacks, логирование в Langfuse
- Dockerfile для LiteLLM

### 1.3 — docker-compose базовый
- Сервисы: PostgreSQL, Redis, LiteLLM, OpenWebUI, Nginx
- `.env.example` с `ANTHROPIC_API_KEY`, `OPENWEBUI_SECRET_KEY`
- Health checks для всех сервисов
- Проверка: `docker compose up` без ошибок, UI доступен

### 1.4 — MWS брендинг и конфиг
- Название и логотип через OpenWebUI env vars
- Системный промпт по умолчанию
- Отключить лишние провайдеры (оставить только LiteLLM)
- Описания моделей в UI

---

## Фаза 2 — Мониторинг (дни 4–5)

**Цель:** Наблюдаемость за запросами, стоимостью и производительностью.

### 2.1 — Prometheus + Grafana
- Добавить в docker-compose: Prometheus (9090), Grafana (3002)
- Настроить scraping метрик LiteLLM и OpenWebUI
- Базовые Grafana дашборды: запросы/мин, latency, ошибки

### 2.2 — Langfuse трейсинг
- Добавить Langfuse (3001) в docker-compose (self-hosted)
- Подключить LiteLLM callback → Langfuse
- Трейсинг: модель, токены, стоимость, latency

---

## Фаза 3 — Memory Companion Service (дни 6–10)

**Цель:** Долгосрочная персистентная память между сессиями.

### 3.1 — FastAPI Memory Microservice
- `memory-service/` — отдельный FastAPI сервис (порт 8001)
- Эндпоинты: `POST /memories`, `GET /memories/{user_id}`, `DELETE /memories/{id}`
- Docker сервис в docker-compose

### 3.2 — pgvector хранилище
- Отдельная БД PostgreSQL с расширением pgvector
- Модели: Memory (id, user_id, content, embedding, created_at, source_conversation_id)
- Семантический поиск через cosine similarity

### 3.3 — Extraction Service
- LLM-powered извлечение фактов из разговора (через LiteLLM Sonnet)
- Дедупликация воспоминаний
- Автоматический вызов после каждого разговора

### 3.4 — OpenWebUI Pipeline
- OpenWebUI Function/Pipeline: инжект релевантных воспоминаний в системный промпт
- UI для просмотра и удаления воспоминаний

---

## Фаза 4 — RAG & Files (дни 11–14)

**Цель:** Работа с документами и базами знаний.

### 4.1 — OpenWebUI RAG (built-in)
- Настроить встроенный RAG в OpenWebUI
- Embedding модель через LiteLLM (или локально)
- Коллекции знаний

### 4.2 — Расширенная обработка файлов
- Форматы сверх стандартного: XLS, XLSX, CSV, аудио
- Companion file-processor если нужно
- OCR для изображений/PDF с изображениями

---

## Фаза 5 — Voice (дни 15–17)

**Цель:** STT + TTS через OpenWebUI.

### 5.1 — Whisper STT
- Настроить OpenWebUI встроенный Whisper
- Или внешний Whisper API через OpenAI-compatible endpoint

### 5.2 — TTS
- edge-tts или OpenAI TTS endpoint
- Настройка через OpenWebUI env vars

---

## Фаза 6 — AI Router Sonnet/Opus (дни 18–20)

**Цель:** Умный автовыбор модели под задачу.

### 6.1 — Правила роутинга
- Sonnet: общие задачи, чат, объяснения
- Opus: сложный код, анализ, длинные документы
- LiteLLM router rules с классификацией по контенту

### 6.2 — Cost Tracking
- Подсчёт токенов и стоимости в Langfuse
- Лимиты на пользователя в LiteLLM

---

## Фаза 7 — Svelte UI Кастомизация (дни 21–23)

**Цель:** MWS-специфичный UI поверх OpenWebUI.

### 7.1 — Брендинг
- Кастомная тема (цвета, шрифты через CSS vars)
- Логотип и название

### 7.2 — Custom Components
- Панель воспоминаний (интеграция с Memory Service)
- Статистика использования моделей
- Кастомные лейблы для Sonnet/Opus

---

## Фаза 8 — Security & Production (дни 24–26)

**Цель:** Готовность к продакшену.

### 8.1 — Безопасность
- OWASP аудит всех сервисов
- Rate limiting на Nginx уровне
- Secrets management (.env + docker secrets)
- HTTPS конфиг Nginx

### 8.2 — Production Compose
- `docker-compose.prod.yml` с ресурсными лимитами
- Healthchecks + restart policies
- Volume backups

---

## Агенты и распределение

| Фаза | Агент |
|------|-------|
| 1.1–1.3 | DevOpsAgent |
| 1.2 | AIRouterAgent |
| 1.4 | OpenWebUIAgent |
| 2.1–2.2 | DevOpsAgent |
| 3.1–3.4 | MemoryAgent + BackendCoderAgent |
| 4.1–4.2 | FileAgent + OpenWebUIAgent |
| 5.1–5.2 | VoiceAgent + OpenWebUIAgent |
| 6.1–6.2 | AIRouterAgent |
| 7.1–7.2 | FrontendAgent (SvelteKit) |
| 8.1–8.2 | SecurityAgent + DevOpsAgent |

---

## Ключевые технологии

| Компонент | Технология |
|-----------|------------|
| UI | SvelteKit (OpenWebUI v0.8.12) |
| AI Gateway | LiteLLM |
| Models | claude-sonnet-4-6, claude-opus-4-6 |
| Memory | FastAPI + pgvector |
| Tracing | Langfuse |
| Metrics | Prometheus + Grafana |
| Proxy | Nginx |
| DB | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
