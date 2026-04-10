# Фаза 2 — Мониторинг: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 2.1 — Prometheus + Grafana
- Prometheus (порт 9090) — scraping метрик LiteLLM каждые 15 секунд
- Grafana (порт 3002) — auto-provisioned datasource (Prometheus) + дашборд "LiteLLM Overview"
- Дашборд содержит: requests/min, latency (p50/p95/p99), errors, tokens/min, spend ($)
- Конфиги в `monitoring/prometheus.yml`, `monitoring/grafana/provisioning/`, `monitoring/grafana/dashboards/`
- Retention: 30 дней

### 2.2 — Langfuse трейсинг
- Langfuse v2 self-hosted (порт 3001)
- Подключён к PostgreSQL БД `langfuse` (создаётся автоматически через init-databases.sql)
- LiteLLM callbacks (`success_callback`, `failure_callback`) уже были в config.yaml — добавлены ключи
- Langfuse секреты (NEXTAUTH_SECRET, SALT) сгенерированы и прописаны в `.env`
- **Проверено:** отправлен запрос через mws/nemotron → 2 трейса появились в Langfuse UI

## Новые сервисы в docker-compose.yml

| Сервис | Image | Порт | Healthcheck |
|--------|-------|------|-------------|
| langfuse | langfuse/langfuse:2 | 3001 | wget /api/public/health |
| prometheus | prom/prometheus:latest | 9090 | — |
| grafana | grafana/grafana:latest | 3002 | — |

## Новые volumes

- `prometheus_data` — данные Prometheus
- `grafana_data` — данные Grafana

## Доступ

- Langfuse: http://localhost:3001 (аккаунт создан, проект создан, ключи в .env)
- Grafana: http://localhost:3002 (admin/admin)
- Prometheus: http://localhost:9090

## Отклонения от плана

- Нет — всё по плану
