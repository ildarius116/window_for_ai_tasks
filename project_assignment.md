# MWS GPT Platform — Project Assignment Prompt

> **Роль:** Ты — опытный техлид и программный архитектор.
> **Задача:** Реализовать платформу **MWS GPT** — единое окно для всех ИИ-задач на базе OpenWebUI.

---

## 🎯 Цель проекта

Создать универсальное веб-приложение, которое объединяет:
- 💬 **Текст** — чат с LLM-моделями (Anthropic, OpenAI, OpenRouter, DashScope) через единый AI-роутер
- 🎙️ **Голос** — Speech-to-Text (встроенный faster-whisper) и Text-to-Speech (gTTS companion)
- 📁 **Файлы** — загрузка, анализ, RAG (встроенный в OpenWebUI, sentence-transformers)
- 🧠 **Долгосрочная память** — персистентный контекст между сессиями (отдельный микросервис)
- 🤖 **Авто-роутинг** — автоматический выбор модели по сложности запроса (complexity_router)
- 📊 **Мониторинг** — трейсинг LLM-вызовов (Langfuse), метрики (Prometheus+Grafana)

---

## 🏗️ Технологический стек

| Слой | Технология | Обоснование |
|------|-----------|-------------|
| **Frontend + Backend** | OpenWebUI (prebuilt image `ghcr.io/open-webui/open-webui:main`) | Готовый SvelteKit UI с чатом, RAG, voice, auth — не требует кастомной сборки |
| **AI Gateway** | LiteLLM Proxy (:4000) | Единый OpenAI-compatible gateway ко всем LLM-провайдерам |
| **Память** | Memory Service (FastAPI + pgvector) | Кастомный микросервис: извлечение фактов, семантический поиск |
| **TTS** | TTS Service (FastAPI + gTTS) | OpenAI-compatible `/v1/audio/speech`, без API-ключей |
| **STT** | faster-whisper (встроен в OpenWebUI) | Локальная модель `base`, без внешнего API |
| **RAG Embeddings** | sentence-transformers/all-MiniLM-L6-v2 (встроен в OpenWebUI) | Локально, без внешнего API |
| **БД** | PostgreSQL 16 + pgvector (`pgvector/pgvector:pg16`) | 4 базы: openwebui, litellm, langfuse, memory |
| **Кэш** | Redis 7 | LiteLLM response cache |
| **Трейсинг** | Langfuse v2 (self-hosted) | Трассировка LLM-вызовов: модель, токены, стоимость, latency |
| **Метрики** | Prometheus + Grafana | Scraping метрик LiteLLM, дашборды |
| **Reverse proxy** | Nginx | Rate limiting, security headers, WebSocket проксирование |
| **Оркестрация** | Docker Compose v2 | 10 сервисов в одном стеке |

---

## 🤖 Архитектура субагентов

Вся разработка ведётся через систему специализированных субагентов. Каждый субагент имеет свои скиллы и зону ответственности.

### Субагенты и их скиллы:

```
СУБАГЕНТ                  ЗОНА ОТВЕТСТВЕННОСТИ                          СКИЛЛЫ
─────────────────────────────────────────────────────────────────────────────
🏛️  ArchitectAgent        Системная архитектура, ADR, C4-диаграммы      backend-architecture-patterns
                                                                          agent-development

🐍  BackendCoderAgent     FastAPI эндпоинты, бизнес-логика, модели      fastapi-backend-development
                                                                          python-patterns-best-practices

🗄️  DatabaseAgent         Схема БД, pgvector, оптимизация запросов      django-architecture-patterns
                                                                          python-patterns-best-practices

🎨  FrontendAgent         OpenWebUI кастомизация (CSS, Functions,       frontend-design
                          Tools), модели с описаниями                   context7-documentation-lookup

🤖  AIRouterAgent         LiteLLM конфиг, роутинг моделей,             agent-development
                          промпт-инжиниринг, RAG-пайплайны              skill-development

🧠  MemoryAgent           Долгосрочная память, векторизация,            python-patterns-best-practices
                          извлечение фактов, OpenWebUI Filter           fastapi-backend-development

🎙️  VoiceAgent            Whisper STT, gTTS TTS, интеграция            python-patterns-best-practices
                          с OpenWebUI audio pipeline                    fastapi-backend-development

📁  FileAgent             RAG настройка, Knowledge Bases,               python-patterns-best-practices
                          chunking параметры                            fastapi-backend-development

🔍  ReviewerAgent         Code review, SOLID/DRY проверки,             python-patterns-best-practices
                          безопасность, производительность              backend-architecture-patterns

🔐  SecurityAgent         Rate limiting, Nginx hardening, secrets       python-patterns-best-practices
                          management, Docker security                   backend-architecture-patterns

🐳  DevOpsAgent           Docker Compose, Nginx конфиг,                backend-architecture-patterns
                          Prometheus/Grafana, backup/restore            agent-development

📚  DocsAgent             README, CLAUDE.md, inline docs,              context7-documentation-lookup
                          Swagger (auto via FastAPI)                    skill-development

🛠️  SkillCreatorAgent     Создание новых скиллов для субагентов,       skill-development
                          обновление промптов, онбординг                agent-development
```

---

## 🔌 MCP-серверы

### Используемые MCP-серверы:

```yaml
mcp_servers:
  # --- Разработка и архитектура ---
  - name: agent-development
    url: https://mcpmarket.com/tools/skills/agent-development-1
    agents: [ArchitectAgent, DevOpsAgent, SkillCreatorAgent]

  - name: skill-development
    url: https://mcpmarket.com/tools/skills/skill-development-2
    agents: [SkillCreatorAgent, AIRouterAgent, DocsAgent]

  - name: backend-architecture-patterns
    url: https://mcpmarket.com/tools/skills/backend-architecture-patterns-1770705486695
    agents: [ArchitectAgent, ReviewerAgent, SecurityAgent, DevOpsAgent]

  # --- Python / FastAPI ---
  - name: python-patterns
    url: https://mcpmarket.com/tools/skills/python-patterns-best-practices
    agents: [BackendCoderAgent, DatabaseAgent, MemoryAgent, VoiceAgent, FileAgent]

  - name: fastapi-development
    url: https://mcpmarket.com/tools/skills/fastapi-backend-development-3
    agents: [BackendCoderAgent, MemoryAgent, VoiceAgent, FileAgent, DatabaseAgent]

  - name: django-architecture  # используется для паттернов ORM и миграций
    url: https://mcpmarket.com/tools/skills/django-architecture-patterns
    agents: [DatabaseAgent]

  # --- Frontend ---
  - name: frontend-design
    url: https://mcpmarket.com/tools/skills/frontend-design-7
    agents: [FrontendAgent]

  # --- Документация ---
  - name: context7-docs
    url: https://mcpmarket.com/tools/skills/context7-documentation-lookup-2
    agents: [DocsAgent, FrontendAgent, AIRouterAgent]

  # --- Дополнительные (рекомендуемые) ---
  - name: docker-compose-patterns
    url: https://mcpmarket.com/tools/skills/docker-compose-devops
    agents: [DevOpsAgent]

  - name: postgresql-optimization
    url: https://mcpmarket.com/tools/skills/postgresql-performance
    agents: [DatabaseAgent, ReviewerAgent]

  - name: security-owasp
    url: https://mcpmarket.com/tools/skills/security-best-practices
    agents: [SecurityAgent, ReviewerAgent]

  - name: langchain-rag
    url: https://mcpmarket.com/tools/skills/langchain-rag-patterns
    agents: [AIRouterAgent, MemoryAgent, FileAgent]
```

---

## 📐 Архитектура системы (C4 — уровень контейнеров)

```
┌──────────────────────────────────────────────────────────────────────┐
│                      Docker Compose Network (mws-network)            │
│                                                                      │
│  ┌──────────┐    ┌──────────────────────────────────────────────┐    │
│  │  Nginx   │───▶│  OpenWebUI  (prebuilt image)                 │    │
│  │  :80     │    │  :8080 internal / :3000 host                 │    │
│  └──────────┘    │  SvelteKit UI + Auth + RAG + Whisper STT     │    │
│                  └──────────────┬───────────────────────────────┘    │
│                                 │                                     │
│                    ┌────────────┼────────────┐                       │
│                    ▼            ▼            ▼                       │
│  ┌─────────────────────┐  ┌──────────┐  ┌──────────────┐           │
│  │  LiteLLM Proxy      │  │  Memory  │  │  TTS Service │           │
│  │  :4000 (AI Router)  │  │  Service │  │  :8000       │           │
│  │                     │  │  :8000   │  │  (gTTS)      │           │
│  └────────┬────────────┘  └──────────┘  └──────────────┘           │
│           │                                                          │
│    ┌──────┼──────────────┐                                          │
│    ▼      ▼              ▼                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌─────────────┐    │
│  │Anthropic │  │ OpenAI   │  │ OpenRouter   │  │ DashScope   │    │
│  │ Sonnet   │  │ GPT-4o   │  │ Nemotron,    │  │ Qwen Plus   │    │
│  │ Opus     │  │ o3-mini  │  │ Qwen (free)  │  │             │    │
│  └──────────┘  └──────────┘  └──────────────┘  └─────────────┘    │
│                                                                      │
│  ┌────────────┐  ┌───────┐  ┌──────────────────────────────────┐   │
│  │ PostgreSQL │  │ Redis │  │  Мониторинг                      │   │
│  │ pgvector   │  │ :6379 │  │  Langfuse (:3001)                │   │
│  │ :5432      │  │       │  │  Prometheus (:9090)               │   │
│  │ 4 БД:      │  │       │  │  Grafana (:3002)                  │   │
│  │  openwebui │  │       │  └──────────────────────────────────┘   │
│  │  litellm   │  │       │                                          │
│  │  langfuse  │  │       │                                          │
│  │  memory    │  │       │                                          │
│  └────────────┘  └───────┘                                          │
└──────────────────────────────────────────────────────────────────────┘
```

**Поток запроса:**
1. Пользователь → Nginx (:80) → OpenWebUI (:8080)
2. OpenWebUI Filter `mws_memory` (inlet) → ищет в Memory Service релевантные воспоминания → инжектирует в system prompt
3. OpenWebUI → LiteLLM (:4000/v1) как OpenAI-compatible API
4. LiteLLM (complexity_router `mws/auto`) → выбирает Sonnet/Opus по сложности → fallback на free модели
5. LiteLLM → Anthropic / OpenRouter / DashScope
6. LiteLLM → Langfuse (success/failure callbacks)
7. OpenWebUI Filter `mws_memory` (outlet) → периодически извлекает факты через Memory Service

---

## 📁 Структура проекта

```
mws-gpt/
├── docker-compose.yml              # 10 сервисов: полный стек
├── docker-compose.prod.yml         # production overrides (resource limits, logging)
├── .env.example                    # шаблон переменных окружения
├── .env                            # секреты (gitignored)
├── Makefile                        # удобные команды (up, down, build, prod, backup, restore)
├── CLAUDE.md                       # инструкции для Claude Code (актуальная архитектура)
├── PLAN.md                         # план 8 фаз (все выполнены)
├── README.md
├── project_assignment.md           # этот файл
│
├── litellm/                        # AI Gateway (custom Dockerfile)
│   ├── Dockerfile
│   ├── config.yaml                 # 11 моделей, 4 провайдера, complexity_router, fallbacks
│   └── .dockerignore
│
├── memory-service/                 # Companion: долгосрочная память (FastAPI)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── .dockerignore
│   └── app/
│       ├── main.py                 # FastAPI app
│       ├── config.py               # настройки
│       ├── database.py             # SQLAlchemy async engine
│       ├── models.py               # Memory model (pgvector)
│       ├── schemas.py              # Pydantic schemas
│       ├── embedding.py            # hash-based pseudo-embeddings (MVP)
│       ├── extraction.py           # LLM-powered fact extraction
│       └── routers/
│           └── memories.py         # CRUD + search + extract endpoints
│
├── tts-service/                    # Companion: Text-to-Speech (FastAPI + gTTS)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                     # OpenAI-compatible /v1/audio/speech
│   └── .dockerignore
│
├── pipelines/                      # OpenWebUI Functions и Tools (deploy через API)
│   ├── memory_function.py          # Global filter: inlet/outlet для памяти
│   ├── memory_tool.py              # Tool: просмотр/поиск/удаление воспоминаний
│   └── usage_stats_tool.py         # Tool: статистика расходов по моделям
│
├── openwebui/                      # Кастомизация OpenWebUI (volume mounts)
│   └── static/
│       └── custom.css              # MWS тема (indigo accent, стилизация)
│
├── nginx/
│   └── nginx.conf                  # Reverse proxy, rate limiting, security headers
│
├── monitoring/
│   ├── prometheus.yml              # Prometheus scrape config
│   └── grafana/
│       ├── provisioning/
│       │   ├── datasources/
│       │   │   └── datasources.yml
│       │   └── dashboards/
│       │       └── dashboards.yml
│       └── dashboards/
│           └── litellm-overview.json
│
├── scripts/
│   ├── init-databases.sql          # Создание 4 БД (openwebui, litellm, langfuse, memory)
│   ├── setup-models.sh             # Регистрация кастомных моделей в OpenWebUI
│   ├── check-secrets.sh            # Валидация .env (обязательные ключи, слабые пароли, утечки)
│   ├── backup.sh                   # Бэкап всех 4 PostgreSQL баз
│   ├── restore.sh                  # Восстановление конкретной БД из бэкапа
│   └── init-db.sh                  # Legacy init script
│
├── tasks/                          # Файлы задач по подфазам
│   ├── phase-1-1-openwebui-clone-setup.md
│   ├── phase-1-2-litellm-anthropic-config.md
│   └── ...
│
├── tasks_done/                     # Отчёты по завершённым фазам
│   ├── phase-1-done.md
│   ├── phase-2-done.md
│   └── ... (все 8 фаз)
│
└── docs/
    └── project-overview.md
```

---

## 🗄️ Базы данных

Единый PostgreSQL-инстанс (`pgvector/pgvector:pg16`) с 4 базами, создаваемыми автоматически через `scripts/init-databases.sql`:

| База | Управляется | Описание |
|------|-------------|----------|
| `openwebui` | OpenWebUI (автоматические миграции) | Пользователи, чаты, настройки, RAG-файлы |
| `litellm` | LiteLLM (автоматические миграции) | Spend tracking, API keys, user budgets |
| `langfuse` | Langfuse (автоматические миграции) | LLM traces, generations, scores |
| `memory` | Memory Service (SQLAlchemy + pgvector) | Пользовательские воспоминания с vector embeddings |

> **Примечание:** Миграции управляются самими сервисами автоматически. Alembic не используется. Расширение `vector` создаётся в БД `memory` через init-databases.sql.

### Модель Memory Service

```sql
memories: id (UUID), user_id (VARCHAR), content (TEXT),
          embedding (VECTOR(768)), source_chat_id (VARCHAR),
          created_at (TIMESTAMP), updated_at (TIMESTAMP)
```

---

## 🤖 Модели и провайдеры

Определены в `litellm/config.yaml`, доступны через OpenWebUI как `mws/*`:

| Алиас | Провайдер | Модель | Назначение |
|-------|-----------|--------|------------|
| `mws/auto` | LiteLLM complexity_router | Авто-выбор Sonnet/Opus | Дефолт. SIMPLE/MEDIUM→Sonnet, COMPLEX/REASONING→Opus |
| `mws/sonnet` | Anthropic | claude-sonnet-4-6 | Быстрая модель для общих задач |
| `mws/opus` | Anthropic | claude-opus-4-6 | Сложный анализ, код, архитектура |
| `mws/gpt-4o` | OpenAI | gpt-4o | Универсальная модель OpenAI |
| `mws/gpt-4o-mini` | OpenAI | gpt-4o-mini | Быстрая и дешёвая модель OpenAI |
| `mws/o3-mini` | OpenAI | o3-mini | Reasoning модель OpenAI |
| `mws/nemotron` | OpenRouter (free) | nvidia/nemotron-3-super-120b | Free fallback, 120B параметров |
| `mws/nemotron-nano` | OpenRouter (free) | nvidia/nemotron-nano-9b-v2 | Лёгкая и быстрая free модель |
| `mws/qwen-coder` | OpenRouter (free) | qwen/qwen3-coder | Специализация на коде |
| `mws/qwen-or` | OpenRouter (free) | qwen/qwen3-next-80b | 80B free модель |
| `mws/qwen` | DashScope | qwen-plus | Qwen Plus (отдельный API) |

**Fallback-цепочки:**
- `mws/opus` → `mws/sonnet` → `mws/gpt-4o` → `mws/nemotron`
- `mws/sonnet` → `mws/opus` → `mws/gpt-4o` → `mws/nemotron`
- `mws/gpt-4o` → `mws/sonnet` → `mws/nemotron`
- `mws/auto` → `mws/gpt-4o-mini` → `mws/nemotron`

---

## ✅ Фазы разработки (все выполнены)

> Все 8 фаз завершены. Подробные отчёты в `tasks_done/phase-*-done.md`.

---

### 📋 ФАЗА 1 — OpenWebUI + LiteLLM ✅ DONE

**Что сделано:**
- OpenWebUI запущен как prebuilt image (`ghcr.io/open-webui/open-webui:main`)
- LiteLLM настроен с 3 провайдерами и 7 моделями (Anthropic, OpenRouter free, DashScope)
- docker-compose: 5 базовых сервисов (postgres, redis, litellm, openwebui, nginx)
- Брендинг: `WEBUI_NAME: "MWS GPT"`, Ollama отключён
- `.env.example` и healthchecks на всех сервисах

**Отклонение:** Вместо сборки из snapshot v0.8.12 — prebuilt image (проблемы со сборкой: apt-get таймауты, JS heap OOM). Добавлены OpenRouter и DashScope (не в исходном плане) для работы без баланса Anthropic.

---

### 📋 ФАЗА 2 — Мониторинг ✅ DONE

**Что сделано:**
- Prometheus (:9090) — scraping метрик LiteLLM каждые 15 сек
- Grafana (:3002) — auto-provisioned дашборд "LiteLLM Overview" (requests/min, latency p50/p95/p99, errors, tokens/min, spend)
- Langfuse v2 (:3001) — self-hosted, LiteLLM success/failure callbacks
- Retention: Prometheus 30 дней, Langfuse — в PostgreSQL

---

### 📋 ФАЗА 3 — Memory Companion Service ✅ DONE

**Что сделано:**
- `memory-service/` — FastAPI микросервис с pgvector
- Endpoints: POST /memories, GET /memories/{user_id}, POST /memories/search, POST /memories/extract, DELETE
- LLM-powered extraction фактов через `mws/nemotron`
- Дедупликация: cosine similarity > 0.9 → пропуск
- OpenWebUI global filter `mws_memory` (inlet: инжекция памяти в system prompt; outlet: извлечение фактов)

**Отклонение:** Hash-based pseudo-embedding (MVP) вместо реальной embedding модели — free OpenRouter не предоставляет /embeddings endpoint.

---

### 📋 ФАЗА 4 — RAG & Files ✅ DONE

**Что сделано:**
- Встроенный RAG через OpenWebUI + `sentence-transformers/all-MiniLM-L6-v2` (локально)
- Chunk: 1000 символов, overlap 100, top_k 3
- Форматы: PDF, DOCX, TXT, MD, HTML (из коробки OpenWebUI)
- Knowledge Bases через `/api/v1/knowledge/`

---

### 📋 ФАЗА 5 — Voice (STT + TTS) ✅ DONE

**Что сделано:**
- **STT**: встроенный faster-whisper, модель `base` (~140MB), `AUDIO_STT_ENGINE=""`
- **TTS**: новый `tts-service/` (FastAPI + gTTS), OpenAI-compatible `/v1/audio/speech`
- 6 голосов (alloy, echo, fable, onyx, nova, shimmer) через разные gTTS tld

**Отклонение:** edge-tts заблокирован (403), встроенный speecht5_tts сломан — решение через gTTS companion.

---

### 📋 ФАЗА 6 — AI Router (Smart Routing) ✅ DONE

**Что сделано:**
- `mws/auto` — complexity_router с rule-based scoring (sub-ms latency)
- Классификация: code_keywords → COMPLEX (Opus), reasoning_keywords → REASONING (Opus), default → Sonnet
- Fallback-цепочки на free модели при недоступности Anthropic
- Cost tracking в LiteLLM PostgreSQL + Langfuse
- User budget: $10/мес на пользователя

---

### 📋 ФАЗА 7 — UI Customization ✅ DONE

**Что сделано:**
- Custom CSS тема (indigo accent #6366f1), volume-mounted
- 8 кастомных моделей с описаниями в OpenWebUI
- Welcome banner с описанием возможностей
- **Tool: MWS Memory Manager** — просмотр/поиск/удаление воспоминаний из чата
- **Tool: MWS Usage Stats** — статистика расходов по моделям
- System prompt для mws-auto с описанием инструментов

---

### 📋 ФАЗА 8 — Security & Production ✅ DONE

**Что сделано:**
- **Nginx hardening**: rate limiting (3 зоны), security headers (CSP, HSTS prep, X-Frame-Options), блокировка атакующих путей (.env, wp-admin, phpmyadmin)
- **Docker security**: `no-new-privileges` на всех 10 сервисах, read-only FS (nginx, prometheus), внутренние сервисы не экспонируют порты на хост
- **`scripts/check-secrets.sh`**: валидация .env (обязательные ключи, слабые пароли, утечки в git)
- **`docker-compose.prod.yml`**: resource limits (~4.5G RAM, ~6.25 CPU), json-file logging с ротацией, restart: always
- **Backup/restore**: `scripts/backup.sh` (все 4 БД, gzip, автоочистка 7 дней), `scripts/restore.sh`
- **.dockerignore** для всех 3 build-контекстов

---

## 🎬 Команды запуска

```bash
# Первоначальная настройка
cp .env.example .env            # заполнить API-ключи
make gen-secrets                # сгенерировать секреты → скопировать в .env
make build                      # собрать LiteLLM + memory-service + tts-service
make setup                      # запуск стека + инструкции

# Ежедневная работа
make up                         # запуск всех 10 сервисов
make down                       # остановка
make ps                         # статус сервисов
make logs                       # логи всех сервисов
make logs-openwebui             # логи OpenWebUI
make logs-litellm               # логи LiteLLM

# Production
make prod                       # запуск с resource limits и logging

# Бэкапы
make backup                     # бэкап всех 4 БД
make restore DB=memory FILE=backups/memory_2026-03-29_120000.sql.gz

# При изменении моделей
# Редактировать litellm/config.yaml, затем:
docker compose build litellm && docker compose up -d

# При изменении .env
docker compose up -d --force-recreate litellm

# Полный сброс (DESTRUCTIVE)
make reset                      # удалить volumes и пересобрать
```

---

## 📊 KPI проекта

| Метрика | Цель |
|---------|------|
| Время первого ответа | < 1 секунда (зависит от провайдера) |
| Smart routing latency | < 1ms (rule-based, без API) |
| STT latency | < 2 секунды (faster-whisper base) |
| Uptime | > 99.5% |
| User budget | $10/мес (настраиваемо) |
| Production RAM | ~4.5 GB (все 10 сервисов) |

---

## 🔑 Переменные окружения (.env.example)

```env
# === AI провайдеры ===
ANTHROPIC_API_KEY=sk-ant-...       # Anthropic (Sonnet, Opus) — требует баланса
OPENAI_API_KEY=sk-proj-...        # OpenAI (GPT-4o, GPT-4o-mini, o3-mini)
OPENROUTER_API_KEY=sk-or-v1-...   # OpenRouter (free models: Nemotron, Qwen)
QWEN_API_KEY=sk-...                # DashScope / Qwen API

# === LiteLLM ===
LITELLM_MASTER_KEY=                # openssl rand -hex 32

# === OpenWebUI ===
OPENWEBUI_SECRET_KEY=              # openssl rand -hex 32

# === PostgreSQL ===
POSTGRES_USER=mws
POSTGRES_PASSWORD=                 # openssl rand -hex 16
POSTGRES_DB=openwebui

# === Langfuse ===
LANGFUSE_PUBLIC_KEY=               # создать в Langfuse UI
LANGFUSE_SECRET_KEY=               # создать в Langfuse UI
LANGFUSE_HOST=http://langfuse:3000
LANGFUSE_NEXTAUTH_SECRET=         # openssl rand -hex 32
LANGFUSE_SALT=                    # openssl rand -hex 32
```

---

## 🌐 Веб-интерфейсы

| Сервис | URL | Аутентификация |
|--------|-----|----------------|
| OpenWebUI | http://localhost (nginx) или :3000 (direct) | Первый зарегистрированный = admin |
| Langfuse | http://localhost:3001 | Создаётся при первом визите |
| Grafana | http://localhost:3002 | admin / admin (или `GRAFANA_ADMIN_PASSWORD`) |
| Prometheus | http://localhost:9090 | Без аутентификации |
| Memory Service API | http://localhost:8001/docs | Без аутентификации |

---

## 📡 Memory Service API

```
POST   /memories              — сохранить воспоминание (user_id, content, source_chat_id)
GET    /memories/{user_id}    — список воспоминаний пользователя
POST   /memories/search       — семантический поиск (user_id, query, limit)
POST   /memories/extract      — LLM-извлечение фактов из разговора
DELETE /memories/{id}          — удалить конкретное воспоминание
DELETE /memories/user/{id}     — удалить все воспоминания пользователя
GET    /health                — healthcheck
```

---

*Промпт составлен: техлид MWS GPT Platform*
*Версия: 2.0.0 (обновлён по итогам завершения всех 8 фаз)*
*Дата: 2026-03-29*
