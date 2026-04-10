# Фаза 4 — RAG & Files: ЗАВЕРШЕНА

**Дата завершения:** 2026-03-28
**Статус:** DONE

## Что сделано

### 4.1 — RAG (Retrieval-Augmented Generation)
- OpenWebUI использует **встроенный sentence-transformers** (`all-MiniLM-L6-v2`) для embeddings — локально, без внешнего API
- Chunk settings: CHUNK_SIZE=1000, CHUNK_OVERLAP=100, TOP_K=3
- Поддерживаемые форматы из коробки: PDF, DOCX, TXT, MD, HTML
- Файлы загружаются и индексируются через OpenWebUI API (`/api/v1/files/` + `/api/v1/retrieval/process/file`)

### Knowledge Base
- Создана коллекция "General Knowledge" (id: afe703ea-da99-4011-8ce4-e151fa44f397)
- Тестовый файл `test_doc.txt` загружен и добавлен в коллекцию

## E2E тест пройден

Два способа проверены:
1. **Прямая загрузка файла в чат** → вопрос "How many services?" → "9 services" ✓
2. **Через Knowledge Base (#General Knowledge)** → тот же вопрос → "9 services" ✓

Оба раза модель корректно нашла источник и процитировала документ.

## Отклонения от плана

- **Embedding engine**: вместо OpenAI `text-embedding-3-small` через LiteLLM используется встроенный `sentence-transformers/all-MiniLM-L6-v2` — free OpenRouter модели не предоставляют /embeddings endpoint, а встроенная модель работает локально без API
- **Companion file-processor для XLS/OCR**: не реализован — базовые форматы (PDF, DOCX, TXT) работают из коробки, расширенные можно добавить позже
- **Chunk settings**: оставлены дефолты (1000/100) — API update требует полную структуру конфига, настройка через Admin UI
