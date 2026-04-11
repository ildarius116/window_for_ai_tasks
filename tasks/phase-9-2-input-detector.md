# Task: phase-9-2 — Input Detector (rules-based)

## Goal
Добавить в `auto_router_function.py` чистую функцию `_detect(messages, files) -> DetectedInput`, которая без вызовов LLM определяет: есть ли прикреплённые изображения/аудио/документы, содержит ли текст URL, какой язык доминирует, и есть ли ключи "нарисуй"/"поищи в интернете".

## Context
Детектор — первый шаг классификации. Короткие правила закрывают 70-80% кейсов мгновенно и экономят вызов LLM-классификатора. См. `PLAN_chat_agents.md` раздел 5.1.

## Scope
- Dataclass `DetectedInput` (поля см. PLAN_chat_agents.md §5.1).
- Функция `_detect(messages: list, files: list) -> DetectedInput` внутри класса Pipe (приватный метод `self._detect`).
- Правила:
  - `has_image = True` если в `files` есть элемент с `type.startswith("image/")` ИЛИ в последнем `user`-сообщении `content` — список с элементом `{"type": "image_url", ...}`.
  - `has_audio = True` если в `files` есть элемент с `type.startswith("audio/")`.
  - `has_document = True` если имя файла matches `\.(pdf|docx?|txt|md|rtf)$`.
  - `urls = re.findall(r"https?://\S+", last_user_text)`.
  - `lang = "ru"` если доля кириллических символов в `last_user_text` > 0.3, иначе `"en"`.
  - `wants_image_gen = True` если regex `(?i)\b(нарисуй|сгенерируй\s+картинк|draw|generate\s+image|make\s+an?\s+image)\b` match'ит `last_user_text`.
  - `wants_web_search = True` если regex `(?i)\b(найди\s+в\s+интернете|поищи\s+в\s+сети|search\s+the\s+web|look\s+up\s+online|актуальн)\b` match'ит.
- Извлечение `last_user_text` — последнее сообщение с `role == "user"`; если `content` — список, собрать все `type == "text"` части в одну строку.
- Извлечение attachments по типам в отдельные списки `image_attachments`, `audio_attachments`, `document_attachments`.

## Files
- `pipelines/auto_router_function.py` (изменить — добавить dataclass и метод)

## Acceptance criteria
1. Функция `_detect` синхронная, без вызовов сети.
2. Unit-тесты (опционально, в docstring как doctest) или ручная проверка:
   - Пустое сообщение → `lang="en"`, все флаги False.
   - "Расскажи про Python" → `lang="ru"`, всё False.
   - "Нарисуй кота" → `wants_image_gen=True`, `lang="ru"`.
   - Сообщение с файлом `contract.pdf` → `has_document=True`.
   - "Check https://example.com" → `urls=["https://example.com"]`.
3. Функция не падает на пустом `files` или `messages`.
4. `DetectedInput` сериализуется в dict (для debug-логов и routing-блока).

## Dependencies
- phase-9-1 (каркас Pipe).

## Out of scope
- LLM-классификатор (phase-9-3).
- Использование detected в pipe() — будет в phase-9-3/9-9.
