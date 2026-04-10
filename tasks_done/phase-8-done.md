# Фаза 8 — Security & Production: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-29
**Статус:** DONE

## Что сделано

### 8.1 — Security

#### Nginx Hardening (`nginx/nginx.conf`)

**Rate Limiting** — три зоны ограничения запросов:
- `general` — 10 req/s на IP (burst=20), применяется к `/`
- `api` — 5 req/s на IP (burst=10), применяется к `/api/` и `/litellm/`
- `conn_per_ip` — лимит 50 одновременных соединений на IP
- Все лимиты возвращают HTTP 429 (Too Many Requests) вместо стандартного 503

**Security Headers** — на все ответы добавлены:
- `X-Frame-Options: SAMEORIGIN` — защита от clickjacking
- `X-Content-Type-Options: nosniff` — запрет MIME sniffing
- `X-XSS-Protection: 1; mode=block` — XSS фильтрация
- `Referrer-Policy: strict-origin-when-cross-origin` — контроль Referer
- `Content-Security-Policy` — ограничение источников контента (scripts, styles, images, fonts, connect, media, frame-ancestors)

**Блокировка атакующих путей:**
- Файлы: `.env`, `.git`, `.svn`, `.htaccess`, `.htpasswd`, `.DS_Store` — deny all + 404
- Пути: `wp-admin`, `wp-login`, `wp-content`, `wp-includes`, `xmlrpc.php`, `phpmyadmin`, `myadmin`, `pma`, `admin/config`, `cgi-bin` — deny all + 404

**HTTPS подготовка** — закомментированный блок для SSL:
- TLS 1.2/1.3 с современными ciphersuites (ECDHE-ECDSA/RSA-AES128/256-GCM-SHA256/384)
- Подготовлен HSTS header
- HTTP-to-HTTPS redirect (301)
- Пути для сертификатов: `/etc/nginx/ssl/fullchain.pem`, `/etc/nginx/ssl/privkey.pem`

#### Secrets Management (`scripts/check-secrets.sh`)

Скрипт проверки секретов с 5 уровнями валидации:

1. **Проверка .env** — существование файла
2. **Required keys** — 7 обязательных ключей (ANTHROPIC_API_KEY, OPENAI_API_KEY, LITELLM_MASTER_KEY, OPENWEBUI_SECRET_KEY, POSTGRES_PASSWORD, LANGFUSE_NEXTAUTH_SECRET, LANGFUSE_SALT)
3. **Optional keys** — 4 рекомендуемых (QWEN_API_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, GRAFANA_ADMIN_PASSWORD)
4. **Weak passwords** — проверка на слабые значения (password, 123456, admin, secret, changeme, default); предупреждение при POSTGRES_PASSWORD < 16 символов; проверка GRAFANA_ADMIN_PASSWORD != "admin"
5. **Leaked secrets** — сканирование git-tracked файлов на паттерны API-ключей (sk-ant-api, sk-or-v1, sk-proj, sk-lf, pk-lf)
6. **Gitignore** — проверка что .env включён в .gitignore

Цветной вывод (RED/YELLOW/GREEN), exit code 1 при ошибках.

#### Docker Security (`docker-compose.yml`)

**Все 10 сервисов** получили `security_opt: no-new-privileges:true` — запрет эскалации привилегий внутри контейнеров.

**Удаление портов из host** — внутренние сервисы (postgres, redis, litellm, memory-service, tts-service, langfuse, prometheus) не экспонируют порты на хост, доступны только через `mws-network`. Комментарии в compose явно указывают на это.

**Read-only файловые системы:**
- `nginx` — `read_only: true` с tmpfs для `/tmp`, `/var/cache/nginx`, `/var/run`
- `prometheus` — `read_only: true` с tmpfs для `/tmp`

#### .dockerignore файлы

Три идентичных `.dockerignore` для build-контекстов `litellm/`, `memory-service/`, `tts-service/`:
- Исключают: `.env`, `.env.*`, `.git`, `__pycache__`, `*.pyc`, `*.pyo`, `.pytest_cache`, `.mypy_cache`, `.venv`, `venv`, `node_modules`, `*.md`, `.dockerignore`, `Dockerfile`, `docker-compose*.yml`
- Предотвращают утечку секретов и ненужных файлов в Docker image

### 8.2 — Production Compose

#### docker-compose.prod.yml

Override-файл для production (`docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d`):

**Logging** — единый шаблон через YAML anchor (`x-logging`):
- Драйвер: `json-file`
- Ротация: `max-size: 10m`, `max-file: 3` (30MB на сервис)
- Применён ко всем 10 сервисам

**Resource Limits** (deploy.resources.limits):

| Сервис | Memory | CPU |
|--------|--------|-----|
| postgres | 1G | 1.0 |
| openwebui | 1G | 1.0 |
| litellm | 512M | 1.0 |
| memory-service | 512M | 0.5 |
| langfuse | 512M | 0.5 |
| redis | 256M | 0.5 |
| tts-service | 256M | 0.5 |
| prometheus | 256M | 0.5 |
| grafana | 256M | 0.5 |
| nginx | 128M | 0.25 |
| **Итого** | **~4.5G** | **~6.25** |

**Restart Policy** — все сервисы переведены на `restart: always` (вместо `unless-stopped` в dev)

#### Backup/Restore скрипты

**`scripts/backup.sh`:**
- Дамп всех 4 баз данных: openwebui, litellm, langfuse, memory
- Формат: gzip-сжатый SQL (`{db}_{date}.sql.gz`)
- Сохранение в `backups/` с timestamp
- Автоочистка: удаление бэкапов старше 7 дней (`find -mtime +7 -delete`)

**`scripts/restore.sh`:**
- Восстановление конкретной БД из gzip-дампа
- Usage: `./scripts/restore.sh <database> <backup_file>`
- Интерактивное подтверждение перед перезаписью данных (`read -p`)

#### Makefile targets

Добавлены новые targets:

| Target | Описание |
|--------|----------|
| `make prod` | Запуск с production overrides |
| `make backup` | Бэкап всех 4 БД |
| `make restore DB=... FILE=...` | Восстановление конкретной БД |

## Файлы создано/изменено

- `nginx/nginx.conf` — rate limiting, security headers, attack path blocking, HTTPS prep
- `docker-compose.yml` — security_opt, read_only, port removal, tmpfs
- `docker-compose.prod.yml` — resource limits, logging, restart: always (новый файл)
- `scripts/check-secrets.sh` — валидация секретов и сканирование утечек (новый файл)
- `scripts/backup.sh` — бэкап PostgreSQL (новый файл)
- `scripts/restore.sh` — восстановление PostgreSQL (новый файл)
- `Makefile` — targets: prod, backup, restore
- `litellm/.dockerignore` — исключения для Docker build (новый файл)
- `memory-service/.dockerignore` — исключения для Docker build (новый файл)
- `tts-service/.dockerignore` — исключения для Docker build (новый файл)
