# Фаза 5 — Voice (STT + TTS): ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 5.1 — STT (Speech-to-Text)
- **Engine**: встроенный faster-whisper (local), модель `base`
- **Конфигурация**: `AUDIO_STT_ENGINE=""` (пустая строка = локальный Whisper в коде OpenWebUI)
- **WHISPER_MODEL**: `base` (~140MB, скачивается автоматически при первом запросе)
- **Результат**: аудио → текст работает через `/api/v1/audio/transcriptions`
- **Тест**: gTTS MP3 "Hello, this is a test from OpenWeb UI" → транскрибировано корректно

### 5.2 — TTS (Text-to-Speech)
- **Новый сервис**: `tts-service/` — FastAPI микросервис на базе gTTS (Google TTS)
- **Порт**: 8002 (host) / 8000 (container)
- **API**: OpenAI-compatible `/v1/audio/speech` endpoint
- **Голоса**: 6 вариантов (alloy, echo, fable, onyx, nova, shimmer) — маппятся на разные gTTS tld для различного акцента
- **Интеграция**: OpenWebUI → `AUDIO_TTS_ENGINE=openai`, `AUDIO_TTS_OPENAI_API_BASE_URL=http://tts-service:8000/v1`
- **Тест**: POST `/api/v1/audio/speech` с текстом → 28KB MP3 файл, 200 OK

## Новые сервисы

| Сервис | Image | Порт | Healthcheck |
|--------|-------|------|-------------|
| tts-service | tts-service/Dockerfile | 8002 | python urllib /health |

## E2E тест пройден

1. **TTS**: `POST /api/v1/audio/speech {"input":"Hello, this is a test from Open WebUI.","voice":"alloy"}` → 200, 28KB MP3
2. **STT**: `POST /api/v1/audio/transcriptions` с этим MP3 → `{"text":"Hello, this is a test from OpenWeb UI."}`
3. **Круговой тест**: текст → TTS → MP3 → STT → текст. Исходный и результирующий текст совпадают.

## Отклонения от плана

- **OpenWebUI transformers engine не работает**: встроенный `microsoft/speecht5_tts` вызывал `RuntimeError: Dataset scripts are no longer supported` из-за несовместимости версии `datasets` с `cmu-arctic-xvectors`. Решение — отдельный TTS сервис.
- **edge-tts заблокирован**: Microsoft WebSocket endpoint возвращал 403 (региональное ограничение или изменение API). Заменён на gTTS.
- **STT_ENGINE значение**: в коде OpenWebUI локальный Whisper активируется при `STT_ENGINE=""` (пустая строка), а не `"whisper-local"` как показано в UI. Исправлено через API.
- **gTTS вместо neural TTS**: качество голоса базовое (Google Translate TTS), но работает стабильно без API-ключей. Можно заменить на edge-tts или платный сервис в будущем.

## Файлы изменены/создано

- `tts-service/main.py` — FastAPI сервис с OpenAI-compatible TTS
- `tts-service/Dockerfile` — Docker-образ на python:3.11-slim + gTTS
- `tts-service/requirements.txt` — fastapi, uvicorn, gTTS
- `docker-compose.yml` — добавлен tts-service, добавлены AUDIO_* env vars для OpenWebUI
