# Phase 8.1 — Безопасность и Production Readiness

**Агент:** SecurityAgent + DevOpsAgent
**Зависимости:** все предыдущие
**Статус:** TODO

## Безопасность

### OWASP аудит

Проверить по чеклисту:
- [ ] A01 Broken Access Control — все endpoint'ы memory-service требуют auth
- [ ] A02 Cryptographic Failures — .env не в git, HTTPS настроен
- [ ] A03 Injection — нет raw SQL запросов, используем ORM
- [ ] A05 Security Misconfiguration — убрать дефолтные пароли
- [ ] A07 Auth Failures — rate limiting на /login
- [ ] A09 Logging Failures — все запросы логируются в Langfuse

### Nginx Security Headers

```nginx
add_header X-Frame-Options DENY;
add_header X-Content-Type-Options nosniff;
add_header Referrer-Policy strict-origin-when-cross-origin;
add_header Content-Security-Policy "default-src 'self'";
```

### Secrets

- Все секреты через `.env` (не хардкодить)
- `.env` в `.gitignore`
- `make gen-secrets` — скрипт генерации всех ключей

## Production docker-compose

`docker-compose.prod.yml` добавить:
```yaml
# Resource limits
deploy:
  resources:
    limits:
      memory: 2G
      cpus: '1.0'

# Restart policy
restart: unless-stopped

# No exposed ports (только через nginx)
```

## HTTPS (Nginx)

Конфиг Nginx с Let's Encrypt или self-signed cert для локального:
- SSL termination на Nginx
- Redirect HTTP → HTTPS
- Проксирование на OpenWebUI и другие сервисы

## Критерии готовности

- [ ] OWASP чеклист пройден, критических пунктов нет
- [ ] HTTPS работает (самоподписанный cert для dev)
- [ ] Security headers присутствуют
- [ ] `make gen-secrets` создаёт все нужные ключи
- [ ] `docker-compose.prod.yml` поднимает стек без exposed портов кроме 80/443
