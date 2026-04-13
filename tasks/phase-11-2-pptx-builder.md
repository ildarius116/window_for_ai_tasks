# phase-11-2 — pptx builder (рендер из схемы)

## Цель
Реализовать чистую функцию `build_pptx(schema) -> bytes`, которая по pydantic-схеме собирает валидный `.pptx` на дефолтном шаблоне `python-pptx`. Без LLM и без парсинга — только рендер.

## Что сделать
- `pptx-service/models.py` — pydantic-модели:
  ```python
  class Slide(BaseModel):
      title: str
      bullets: list[str] = []
      notes: str | None = None

  class PresentationSchema(BaseModel):
      title: str
      subtitle: str | None = None
      slides: list[Slide]
      style: str = "mws"
  ```
- `pptx-service/builder.py` — `def build_pptx(schema: PresentationSchema) -> bytes`:
  - `Presentation()` (дефолтный шаблон, без mws_template).
  - Слайд 0: layout `Title Slide`, заполняется `title` + `subtitle`.
  - Каждый `schema.slides[i]`: layout `Title and Content`, буллеты добавляются как параграфы в `placeholder[1].text_frame`, первый — `text_frame.text = ...`, остальные — `add_paragraph()`.
  - `notes`: `slide.notes_slide.notes_text_frame.text = slide.notes` если задано.
  - Сохранение в `BytesIO`, возврат `.getvalue()`.
- `pptx-service/tests/test_builder.py` — один юнит-тест:
  - фикстура с 3 слайдами,
  - вызывает `build_pptx`,
  - проверяет что bytes начинаются с сигнатуры zip (`PK\x03\x04`),
  - перечитывает файл через `Presentation(BytesIO(data))` и проверяет число слайдов и заголовки.

## Критерии готовности
- `pytest pptx-service/tests/` — зелёный.
- Файл, сохранённый на диск, открывается в LibreOffice/PowerPoint без предупреждений.
- На тестовой схеме (Title Slide + 3 контентных) получаем 4 слайда в итоговом файле.

## Затронутые файлы
- `pptx-service/models.py` (new)
- `pptx-service/builder.py` (new)
- `pptx-service/tests/test_builder.py` (new)

## Зависит от
- phase-11-1
