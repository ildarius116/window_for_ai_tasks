# phase-12-2 — Схемы данных и константы fact-checker'а

## Цель
Ввести в `pipelines/auto_router_function.py` типы данных (`Claim`, `UrlStatus`, `FactCheckReport`) и константы, которыми будут пользоваться все последующие задачи.

## Что сделать
- Рядом с существующими `@dataclass`-ами (`SubTask`, `CompactResult`) добавить:
  ```python
  @dataclass
  class UrlStatus:
      url: str
      status: str                 # url_ok | url_redirect | url_404 |
                                  # url_unreachable | url_auth_required | url_blocked_ssrf
      http_code: Optional[int] = None
      final_url: Optional[str] = None
      snippet: str = ""           # первые ~2KB текста при url_ok — для LLM-вердиктора
      error: str = ""

  @dataclass
  class Claim:
      text: str
      source_kind: str            # kind субагента, из чьего summary извлечён claim
      verdict: str = "unknown"    # proven | plausible | fabricated | unknown
      evidence_url: Optional[str] = None
      reason: str = ""

  @dataclass
  class FactCheckReport:
      urls: list[UrlStatus] = field(default_factory=list)
      claims: list[Claim] = field(default_factory=list)
      total_checked_kinds: list[str] = field(default_factory=list)
      error: Optional[str] = None
  ```
- Ниже — константы:
  ```python
  _CHECKABLE_KINDS = {
      "web_search", "web_fetch", "deep_research", "memory_recall", "doc_qa",
  }
  _FACT_CHECK_TRIGGER_RE = re.compile(
      r"(?i)(провер\w*\s+(факты|источник\w*)|fact[-\s]?check|verify\s+(the\s+)?(claims|sources|facts))"
  )
  _SSRF_BLOCK_RE = re.compile(
      r"^(https?://)?(localhost|127\.|10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)"
  )
  ```
- `@dataclass`-и не должны ломать сериализацию существующего кода — в `CompactResult.metadata` уже `dict`, туда будем класть `FactCheckReport.__dict__` через `asdict()` только при финальной сборке отчёта. Никаких правок существующих схем.

## Критерии готовности
- Файл импортируется без ошибок (пайп грузится в OpenWebUI).
- `from dataclasses import asdict` уже импортирован — не ломаем импорты.
- Регексы компилируются (проверить локально `python -c "from pipelines.auto_router_function import *"`).

## Затронутые файлы
- `pipelines/auto_router_function.py`
