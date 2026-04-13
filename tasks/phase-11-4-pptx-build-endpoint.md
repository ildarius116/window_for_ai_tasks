# phase-11-4 — POST /build endpoint

## Цель
Собрать реальный `POST /build` в pptx-service: multipart с файлом + инструкцией → парсинг → LLM-схема → рендер → `StreamingResponse` с `.pptx`.

## Что сделать
- В `pptx-service/main.py` заменить заглушку `POST /build` на:
  ```python
  @app.post("/build")
  async def build(
      file: UploadFile | None = File(None),
      user_instruction: str = Form(""),
      source_text: str | None = Form(None),
  ):
      ...
  ```
- Логика:
  1. Если `file` передан — прочитать bytes, проверить размер ≤ 20 МБ (413 иначе), извлечь текст через `extract_text`.
  2. Иначе если `source_text` передан — использовать его.
  3. Иначе если только `user_instruction` не пустой — использовать как источник (генерация «из головы»).
  4. Если нигде нет контента — 400 `{"detail":"empty input"}`.
  5. `schema = await generate_schema(source_text, user_instruction)` — на `SchemaGenerationError` → 502.
  6. `data = build_pptx(schema)`.
  7. `return StreamingResponse(BytesIO(data), media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation", headers={"Content-Disposition": f"attachment; filename=\"{slug(schema.title)}.pptx\"", "X-Slide-Count": str(len(schema.slides)+1), "X-Title": schema.title})`.
- Добавить middleware для лога длительности каждого запроса (по образцу memory-service).

## Критерии готовности
- `curl -F "file=@resume.pdf" -F "user_instruction=Сделай презентацию" http://pptx-service:8000/build -o out.pptx` возвращает валидный файл, открывается в LibreOffice.
- `curl -F "user_instruction=Сделай презентацию про async/await на 5 слайдов" http://pptx-service:8000/build -o out.pptx` — тоже работает (без файла).
- Пустой запрос → 400. Файл > 20 МБ → 413. Падение LiteLLM (если отключить) → 502.

## Затронутые файлы
- `pptx-service/main.py`

## Зависит от
- phase-11-3
