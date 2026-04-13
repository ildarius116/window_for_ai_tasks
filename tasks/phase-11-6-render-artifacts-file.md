# phase-11-6 — _render_artifacts + strip для file-артефактов

## Цель
Расширить `_render_artifacts` на тип `"file"` и обновить буферизацию в `_stream_aggregate`, чтобы модель-агрегатор не могла продублировать нашу ссылку на .pptx. Не ломать существующий image-путь.

## Что сделать
- В `pipelines/auto_router_function.py:1103` `_render_artifacts` добавить ветку:
  ```python
  elif t == "file" and art.get("url"):
      name = art.get("filename") or "file"
      out.append(f"\n📎 [{name}]({art['url']})")
  ```
- В `_stream_aggregate` (строка ~174), в блоке `if has_artifacts:` после существующего `re.sub(r"!\[...\]\(...\)", ...)` добавить удаление markdown-ссылок, указывающих на наш файл. Достаточно локально:
  ```python
  text = re.sub(r"\[[^\]]*\]\((?:/api/v1/files/[^)]+|[^)]*\.pptx[^)]*)\)", "", text)
  ```
  Обычные внешние ссылки (например, на GitHub из web_fetch) не трогаем.
- Убедиться, что при `results` с обоими типами артефактов (image + file) оба рендерятся корректно.

## Критерии готовности
- Юнит-тест в `pipelines/tests/test_render_artifacts.py` (новый файл, если таких тестов ещё нет — положить рядом или создать минимальную папку):
  - `CompactResult` с `[{"type":"file","url":"/api/v1/files/abc/content","filename":"r.pptx"}]` → в рендере есть `📎 [r.pptx](/api/v1/files/abc/content)`.
  - Строка `"текст [r.pptx](/api/v1/files/abc/content) конец"` после strip → `"текст  конец"`.
  - Строка `"смотри https://github.com/x/y"` после strip не изменилась.

## Затронутые файлы
- `pipelines/auto_router_function.py`
- `pipelines/tests/test_render_artifacts.py` (new, если не существует)

## Зависит от
- ни от чего (можно делать параллельно с phase-11-1..4)
