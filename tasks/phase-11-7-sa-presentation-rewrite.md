# phase-11-7 — Переписать _sa_presentation

## Цель
Заменить v1-заглушку `_sa_presentation` (auto_router_function.py:1670) на рабочий сабагент: читает исходник (если приложен документ), шлёт его в `pptx-service/build`, заливает результат в OpenWebUI Files API, возвращает `CompactResult` с file-артефактом. На любой ошибке — текстовый fallback.

## Что сделать
- В `Pipe` подготовить метод:
  ```python
  async def _sa_presentation(self, task: SubTask) -> CompactResult:
      trace_id = task.metadata.get("trace_id") if task.metadata else None
      # 1. Собираем исходник: если есть documents — читаем первый файл с диска
      src_bytes, src_name, src_mime = None, None, None
      for att in (task.attachments or []):
          path = att.get("path")
          if path and os.path.exists(path):
              with open(path, "rb") as f:
                  src_bytes = f.read()
              src_name = att.get("filename") or "source.bin"
              src_mime = att.get("content_type") or "application/octet-stream"
              break
      # 2. POST в pptx-service
      url = "http://pptx-service:8000/build"
      data = {"user_instruction": task.input_text or "Сделай презентацию"}
      files = {"file": (src_name, src_bytes, src_mime)} if src_bytes else None
      try:
          async with httpx.AsyncClient(timeout=180) as cli:
              r = await cli.post(url, data=data, files=files)
          if r.status_code != 200:
              return self._presentation_text_fallback(task, reason=f"pptx-service {r.status_code}")
          pptx_bytes = r.content
          title = r.headers.get("X-Title") or "presentation"
          slide_count = r.headers.get("X-Slide-Count") or "?"
      except Exception as e:
          return self._presentation_text_fallback(task, reason=str(e))
      # 3. Upload в OWUI Files API
      safe_name = _slug(title) + ".pptx"
      uploaded = await self._upload_to_owui_files(
          pptx_bytes, safe_name,
          "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      )
      if not uploaded or not uploaded.get("id"):
          return self._presentation_text_fallback(task, reason="owui upload failed")
      file_id = uploaded["id"]
      return CompactResult(
          kind="presentation",
          summary=f"Готова презентация «{title}» — {slide_count} слайдов.",
          artifacts=[{
              "type": "file",
              "url": f"/api/v1/files/{file_id}/content",
              "filename": safe_name,
              "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
          }],
      )
  ```
- `_presentation_text_fallback` — вспомогательный метод, вызывает обычный chat-completion с инструкцией «сделай текстовую структуру слайдов», возвращает `CompactResult(kind="presentation", summary=<markdown>)` **без** artifacts. Модель: `mws/glm-4.6` для документа, иначе default aggregator.
- `_slug(title)` — ASCII-транслитерация + `re.sub(r'\W+', '_', ...)`, результат обрезается до 60 символов.

## Критерии готовности
- С приложенным PDF: ответ содержит файловую ссылку `📎 [<title>.pptx](...)`, реальный файл скачивается и открывается.
- Без приложения: `user_instruction` передаётся в pptx-service, файл генерируется из знаний модели.
- При выключенном pptx-service: сабагент возвращает текстовый markdown-план (fallback), без 500.
- При отсутствии `OWUI_ADMIN_TOKEN`: тот же fallback + предупреждение в `summary` («Файл .pptx сгенерирован, но не может быть загружен в чат: не задан OWUI_ADMIN_TOKEN»).

## Затронутые файлы
- `pipelines/auto_router_function.py`

## Зависит от
- phase-11-4 (endpoint должен работать)
- phase-11-5 (upload helper)
- phase-11-6 (render artifacts)
