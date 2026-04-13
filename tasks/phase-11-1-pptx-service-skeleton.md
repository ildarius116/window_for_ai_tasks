# phase-11-1 — pptx-service skeleton

## Цель
Поднять новый микросервис `pptx-service` (FastAPI) по образцу `tts-service`, чтобы следующие задачи могли добавлять к нему логику.

## Что сделать
- Создать директорию `pptx-service/` со структурой:
  - `Dockerfile` — `python:3.11-slim`, устанавливает зависимости из `requirements.txt`, запускает `uvicorn main:app --host 0.0.0.0 --port 8000`.
  - `requirements.txt` — `fastapi==0.115.6`, `uvicorn[standard]==0.34.0`, `pydantic==2.10.3`, `httpx==0.28.1`, `python-pptx==1.0.2`, `pypdf==5.1.0`, `python-docx==1.1.2`.
  - `main.py` — FastAPI app с `GET /health` (возвращает `{"status":"ok"}`) и заглушкой `POST /build` (возвращает 501 Not Implemented).
- В `docker-compose.yml` добавить сервис `pptx-service`:
  - `build: ./pptx-service`
  - `networks: [mws-network]`, без `ports` (внутренний)
  - `healthcheck`: python urllib на `http://localhost:8000/health` (по образцу memory-service)
  - `environment: { LITELLM_URL: http://litellm:4000, LITELLM_API_KEY: ${LITELLM_MASTER_KEY}, SCHEMA_MODEL: mws/glm-4.6 }`
- В `Makefile` target `build` добавить `pptx-service` в список собираемых образов.

## Критерии готовности
- `docker compose up -d --build pptx-service` — контейнер healthy.
- `docker compose exec openwebui curl -s http://pptx-service:8000/health` → `{"status":"ok"}`.
- `POST /build` возвращает 501.

## Затронутые файлы
- `pptx-service/Dockerfile` (new)
- `pptx-service/requirements.txt` (new)
- `pptx-service/main.py` (new)
- `docker-compose.yml`
- `Makefile`
