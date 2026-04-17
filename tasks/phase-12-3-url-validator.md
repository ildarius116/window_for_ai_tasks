# phase-12-3 — URL-валидатор с SSRF-защитой

## Цель
Реализовать помощник `_validate_urls(urls) -> list[UrlStatus]`, который параллельно проверяет доступность URL'ов из ответов субагентов, с жёсткими таймаутами и защитой от SSRF.

## Что сделать
- В `pipelines/auto_router_function.py` в классе `Pipe` добавить два метода:
  ```python
  @staticmethod
  def _dedupe_urls(results: list[CompactResult], max_urls: int) -> list[str]:
      seen: set[str] = set()
      ordered: list[str] = []
      for r in results:
          pool = list(r.citations) + _URL_RE.findall(r.summary or "")
          for u in pool:
              u = u.rstrip(").,;:]»\"'")
              if u and u not in seen and not _SSRF_BLOCK_RE.match(u):
                  seen.add(u)
                  ordered.append(u)
                  if len(ordered) >= max_urls:
                      return ordered
      return ordered

  async def _validate_urls(self, urls: list[str]) -> list[UrlStatus]:
      sem = asyncio.Semaphore(8)
      timeout = httpx.Timeout(5.0, connect=3.0)
      async with httpx.AsyncClient(
          timeout=timeout, follow_redirects=False, http2=False
      ) as client:
          async def one(u: str) -> UrlStatus:
              async with sem:
                  try:
                      r = await client.head(u)
                      code = r.status_code
                      if 300 <= code < 400:
                          final = r.headers.get("location", u)
                          # одна попытка GET по редиректу для snippet
                          try:
                              r2 = await client.get(final)
                              snippet = (r2.text or "")[:2048]
                              if r2.status_code < 400:
                                  return UrlStatus(u, "url_redirect", r2.status_code, final, snippet)
                          except Exception:
                              pass
                          return UrlStatus(u, "url_redirect", code, final)
                      if code in (401, 403):
                          return UrlStatus(u, "url_auth_required", code)
                      if 400 <= code < 500:
                          return UrlStatus(u, "url_404", code)
                      if code >= 500:
                          return UrlStatus(u, "url_unreachable", code)
                      # 2xx — забираем snippet через GET, если HEAD не отдал body
                      try:
                          r2 = await client.get(u)
                          snippet = (r2.text or "")[:2048]
                      except Exception:
                          snippet = ""
                      return UrlStatus(u, "url_ok", code, u, snippet)
                  except httpx.ConnectError:
                      return UrlStatus(u, "url_unreachable", None, error="connect")
                  except httpx.TimeoutException:
                      return UrlStatus(u, "url_unreachable", None, error="timeout")
                  except Exception as e:
                      return UrlStatus(u, "url_unreachable", None, error=f"{type(e).__name__}")
          return await asyncio.gather(*[one(u) for u in urls])
  ```
- Метод должен игнорировать URL, заблокированные `_SSRF_BLOCK_RE` (уже отфильтрованы в `_dedupe_urls`, но на всякий случай повторно проверить на входе).
- Не использовать `curl`/`subprocess`. Только `httpx` — он уже есть в проекте.
- В `_render_artifacts`-логику не лезть — это отдельная задача.

## Критерии готовности
- Локальный python-смок на публичных URL:
  - `https://www.wikipedia.org` → `url_ok`, `http_code=200`.
  - `https://rbc.ru/404-fake` → `url_404` (или `url_redirect` на главную — в обоих случаях не ok).
  - `https://nosuch-domain-q8xr5.example` → `url_unreachable`.
  - `http://127.0.0.1:8080/admin` → отфильтрован `_dedupe_urls` (не попадает в `_validate_urls`).
- Нет блокирующих вызовов в event loop (всё async).
- Все запросы укладываются в общий deadline 15s для списка из 12 URL.

## Затронутые файлы
- `pipelines/auto_router_function.py`
