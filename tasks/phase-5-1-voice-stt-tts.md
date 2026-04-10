# Phase 5.1 — Voice: STT + TTS

**Агент:** VoiceAgent + OpenWebUIAgent
**Зависимости:** 1.4
**Статус:** TODO

## Задача

Настроить голосовой ввод (STT) и озвучивание ответов (TTS) через OpenWebUI.

## STT (Speech-to-Text)

OpenWebUI поддерживает OpenAI Whisper API. Настройка:

```yaml
AUDIO_STT_ENGINE: "openai"
AUDIO_STT_OPENAI_API_BASE_URL: "http://litellm:4000/v1"
AUDIO_STT_OPENAI_API_KEY: ${LITELLM_MASTER_KEY}
AUDIO_STT_MODEL: "whisper-1"
```

Альтернатива — локальный Whisper через OpenAI-compatible endpoint (faster-whisper).

## TTS (Text-to-Speech)

```yaml
AUDIO_TTS_ENGINE: "openai"
AUDIO_TTS_OPENAI_API_BASE_URL: "http://litellm:4000/v1"
AUDIO_TTS_OPENAI_API_KEY: ${LITELLM_MASTER_KEY}
AUDIO_TTS_VOICE: "alloy"
```

Или edge-tts как бесплатная альтернатива — нужен companion wrapper с OpenAI-compatible API.

## Если нужен local Whisper

```yaml
# docker-compose добавить:
whisper:
  image: onerahmet/openai-whisper-asr-webservice:latest
  environment:
    ASR_MODEL: base
  ports: ["9000:9000"]
```

## Критерии готовности

- [ ] Кнопка микрофона в OpenWebUI работает
- [ ] Речь транскрибируется в текст
- [ ] Кнопка "воспроизвести ответ" работает
