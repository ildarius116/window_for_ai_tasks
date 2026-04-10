# Phase 4.1 — RAG & Files

**Агент:** FileAgent + OpenWebUIAgent
**Зависимости:** 1.4
**Статус:** TODO

## Задача

Настроить работу с документами: загрузка, индексирование, поиск.

## Встроенный RAG в OpenWebUI

OpenWebUI имеет встроенный RAG. Настроить через env vars:

```yaml
RAG_EMBEDDING_ENGINE: "openai"            # или "ollama"
RAG_EMBEDDING_MODEL: "text-embedding-3-small"
RAG_OPENAI_API_BASE_URL: "http://litellm:4000/v1"
RAG_OPENAI_API_KEY: ${LITELLM_MASTER_KEY}
CHUNK_SIZE: 1500
CHUNK_OVERLAP: 100
```

## Поддерживаемые форматы

OpenWebUI из коробки: PDF, DOCX, TXT, MD, HTML

Добавить обработку через companion (если нужно):
- XLS/XLSX → конвертация в CSV → загрузка в OpenWebUI
- Изображения с текстом → OCR → TXT → загрузка

## Coллекции знаний

Создать через OpenWebUI admin:
- "Общая база знаний" — общие документы компании
- Пользовательские — личные загрузки пользователя

## Критерии готовности

- [ ] Загрузка PDF работает
- [ ] Вопрос по документу получает релевантный ответ с источником
- [ ] Коллекции знаний создаются в admin panel
