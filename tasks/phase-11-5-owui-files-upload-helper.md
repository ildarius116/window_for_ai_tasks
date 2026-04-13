# phase-11-5 — OpenWebUI Files API upload helper

## Цель
Реализовать в пайпе `auto_router_function.py` helper, который заливает bytes в OpenWebUI Files API и возвращает `file_id`/URL, и пробросить нужный токен в env контейнера openwebui.

## Что сделать
- В `docker-compose.yml`, сервис `openwebui.environment`: добавить `OWUI_ADMIN_TOKEN: ${OWUI_ADMIN_TOKEN}`. В `.env.example` строка уже есть; добавить комментарий над ней: «Нужен для pptx-аплоада (phase-11)».
- В `pipelines/auto_router_function.py` рядом с другими helper-методами `Pipe` добавить:
  ```python
  async def _upload_to_owui_files(self, content: bytes, filename: str, mime: str) -> dict | None:
      token = os.getenv("OWUI_ADMIN_TOKEN")
      if not token:
          return None
      url = "http://localhost:8080/api/v1/files/"
      files = {"file": (filename, content, mime)}
      headers = {"Authorization": f"Bearer {token}"}
      async with httpx.AsyncClient(timeout=30) as cli:
          r = await cli.post(url, files=files, headers=headers)
      if r.status_code != 200:
          return None
      return r.json()  # ожидаем {id, filename, ...}
  ```
- При успехе возвращаем dict; при `token is None` или не-200 — `None`. Не кидаем исключение — сабагент сам решит, что делать (fallback).
- Лог: `_log(trace_id, "owui_upload", status=r.status_code, id=...)`.

## Критерии готовности
- Вручную из контейнера `openwebui`:
  ```
  docker compose exec openwebui python -c "
  import httpx, os
  r = httpx.post('http://localhost:8080/api/v1/files/',
                 files={'file':('test.txt', b'hello', 'text/plain')},
                 headers={'Authorization': f'Bearer {os.environ[\"OWUI_ADMIN_TOKEN\"]}'})
  print(r.status_code, r.json())
  "
  ```
  возвращает 200 и `id`.
- При удалении `OWUI_ADMIN_TOKEN` из env helper возвращает `None` и не кидает.

## Затронутые файлы
- `docker-compose.yml`
- `.env.example` (комментарий)
- `pipelines/auto_router_function.py`

## Зависит от
- phase-11-1 (сам сервис не нужен, но задача логически идёт после подготовки скелета)
