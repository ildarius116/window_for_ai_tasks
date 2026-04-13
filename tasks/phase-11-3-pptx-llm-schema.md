# phase-11-3 — Парсер документов + LLM-схема

## Цель
В pptx-service реализовать шаг «исходный файл/текст → `PresentationSchema`»: парсер PDF/DOCX/TXT и вызов LiteLLM с `response_format=json_object`.

## Что сделать
- `pptx-service/parsing.py`:
  - `def extract_text(filename: str, data: bytes) -> str` — по расширению:
    - `.pdf` → `pypdf.PdfReader` по страницам, `page.extract_text()`.
    - `.docx` → `docx.Document`, обход параграфов.
    - `.txt`/`.md` → `data.decode("utf-8", errors="replace")`.
    - иначе `ValueError("unsupported")`.
  - Обрезка до `MAX_CHARS=40000` (с предупреждением в логе).
- `pptx-service/schema_llm.py`:
  - `async def generate_schema(source_text: str, user_instruction: str) -> PresentationSchema`.
  - POST `{LITELLM_URL}/v1/chat/completions`, model = `SCHEMA_MODEL` (default `mws/glm-4.6`), `response_format={"type":"json_object"}`, `max_tokens=2000`, `temperature=0.3`.
  - System prompt (RU+EN, строгие правила):
    - «Верни ТОЛЬКО JSON строго по схеме `{title, subtitle, slides:[{title, bullets, notes}]}`».
    - «Не более 10 слайдов, каждый буллет ≤ 120 символов, 3–6 буллетов на слайд, notes 1–3 предложения, title слайда ≤ 8 слов».
    - Один few-shot пример (резюме → 6 слайдов).
  - User message: `user_instruction` + `\n\n---\n` + `source_text`.
  - На выходе парсинг JSON через `json.loads` → валидация `PresentationSchema(**obj)`. На ошибке валидации — одна ретрай-попытка с добавкой «Your previous response was not valid JSON matching the schema. Try again.»
  - На HTTP/LiteLLM ошибках — `raise SchemaGenerationError`.

## Критерии готовности
- Юнит-тест `tests/test_parsing.py`: извлечение текста из примера PDF (можно положить в `tests/fixtures/resume.pdf`) — возвращает непустой текст с ожидаемыми словами.
- Интеграционный тест `tests/test_schema_llm.py` (опционально, помечен `@pytest.mark.integration`, требует поднятого LiteLLM): реальный вызов, возвращает валидную `PresentationSchema` с `len(slides) >= 3`.

## Затронутые файлы
- `pptx-service/parsing.py` (new)
- `pptx-service/schema_llm.py` (new)
- `pptx-service/tests/fixtures/resume.pdf` (new, маленький тестовый файл)
- `pptx-service/tests/test_parsing.py` (new)
- `pptx-service/tests/test_schema_llm.py` (new)

## Зависит от
- phase-11-2
