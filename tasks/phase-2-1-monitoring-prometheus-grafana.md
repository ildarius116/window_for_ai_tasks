# Phase 2.1 — Prometheus + Grafana

**Агент:** DevOpsAgent
**Зависимости:** 1.3
**Статус:** TODO

## Задача

Добавить observability stack в docker-compose.

## Сервисы для добавления

| Сервис | Image | Порт |
|--------|-------|------|
| prometheus | prom/prometheus:latest | 9090 |
| grafana | grafana/grafana:latest | 3002 |

## Файлы

### `monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: litellm
    static_configs:
      - targets: ['litellm:4000']
    metrics_path: /metrics

  - job_name: openwebui
    static_configs:
      - targets: ['openwebui:8080']
    metrics_path: /metrics
```

### Grafana дашборды (JSON)

- `monitoring/grafana/dashboards/litellm.json` — запросы/мин, latency p95, ошибки по модели
- `monitoring/grafana/dashboards/overview.json` — общий overview

### Grafana provisioning

`monitoring/grafana/provisioning/datasources/prometheus.yaml` — автоподключение Prometheus.

## Критерии готовности

- [ ] Prometheus на :9090 scrape'ит LiteLLM метрики
- [ ] Grafana на :3002 доступна (admin/admin по умолчанию)
- [ ] Дашборд LiteLLM показывает метрики
