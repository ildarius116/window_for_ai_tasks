# PLAN — MWS GPT Auto-Router ("MWS GPT Auto 🎯")

> Мастер-документ дизайна оркестратора чат-агентов для MWS GPT Platform.
> Источник истины для задач `tasks/phase-9-*.md`.
> Дата: 2026-04-11.

---

## 1. Цель и scope

**Цель:** реализовать виртуальную модель `MWS GPT Auto 🎯` в OpenWebUI, которая:

1. Получает любой пользовательский ввод (текст, голос, изображение, файл, URL).
2. Самостоятельно определяет тип задачи и нужные инструменты.
3. Запускает один или несколько субагентов — каждый с подходящей моделью из `model_capabilities.md`.
4. Оркестрирует их работу без засорения своего контекста: оркестратор видит только `CompactResult.summary` каждого субагента, не их сырые ответы.
5. Стримит пользователю единый финальный ответ в markdown.

**Что НЕ входит в v1:**

- Deep Research (многошаговый цикл) — только stub-субагент.
- Генерация презентаций — только stub-субагент.
- Параллельный вызов нескольких chat-моделей с мёржем — финальная chat-модель всегда одна.
- Ручное управление бюджетом токенов/денег — работает через штатные лимиты LiteLLM.
- Замена существующего `memory_function.py` Filter — он остаётся как есть.

**Обязательные фичи xlsx, покрываемые роутером:** 1, 2, 3, 4, 5, 6, 7, 8, 10, 12. Фича #9 (долгосрочная память) закрывается существующим `memory_function.py`. Фича #11 (ручной выбор модели) — штатным dropdown OpenWebUI, без кода.

---

## 2. Поток запроса

```
User (OpenWebUI chat)
      │
      v
[memory_function.Filter.inlet]          ← инъекция памяти (как сейчас)
      │
      v
[auto_router_function.Pipe.pipe]        ← НОВЫЙ файл
      │
      ├─ 1. Detector      (rules: files? audio? image? URL? lang?)
      ├─ 2. Classifier    (правила → LLM-fallback на mws/gpt-oss-20b)
      ├─ 3. Planner       (Plan = list[SubTask])
      │
      v
Dispatcher: asyncio.gather(subagents)   ← каждый субагент делает свой POST к LiteLLM
   │   │   │   │   │   │   │
   v   v   v   v   v   v   v
  VLM STT Code Reas Web Img DocQA
   \___________________________/
      │   каждый возвращает CompactResult(summary ≤ 500 токенов)
      v
Aggregator (final model: mws/t-pro для RU, mws/gpt-alpha для EN)
      │   видит ТОЛЬКО summary субагентов, не их chain-of-thought
      v (stream, async generator)
  <details><summary>🎯 Routing decision</summary>...</details>
  + финальный ответ markdown
      │
      v
[memory_function.Filter.outlet]         ← извлечение памяти (как сейчас)
      v
  User
```

---

## 3. Ключевое инвариантное свойство — изоляция контекста

**Правило:** каждый субагент — свежий HTTP POST к `http://litellm:4000/v1/chat/completions` (или `/v1/audio/transcriptions` / `/v1/images/generations`). Оркестратор хранит только `{kind, summary, citations, artifacts}`. Сырые ответы моделей выбрасываются сразу после суммаризации.

**Зачем:** сохранить токены в финальном aggregate-call и не путать финальную модель chain-of-thought субагентов. Финальная модель получает только оригинальный user-запрос + "scratchpad" из compact-summary с тегами `[sa_vision]`, `[sa_web_search]` и т.п.

---

## 4. Файлы для реализации

**Создать:**

| Путь | Что |
|---|---|
| `pipelines/auto_router_function.py` | Единый Pipe-файл: Valves, `pipes()`, `async pipe()`, детектор, классификатор, диспетчер, 13 субагентов, агрегатор. |
| `scripts/deploy_function.sh` | Curl-обёртка для `POST /api/v1/functions/create` в OpenWebUI. |

**Изменить:**

| Путь | Что |
|---|---|
| `docker-compose.yml` | Добавить `ENABLE_RAG_WEB_SEARCH=true`, `RAG_WEB_SEARCH_ENGINE=duckduckgo` в секцию OpenWebUI. |
| `Makefile` | Добавить target `deploy-functions`. |
| `.env.example` | Добавить `OWUI_ADMIN_TOKEN` — токен админа OpenWebUI для деплоя функций. |
| `CLAUDE.md` | Короткая секция про auto-router. |
| `README_proj.md` | Секция "Как пользоваться MWS GPT Auto". |

**Не трогать:** `pipelines/memory_function.py`, `pipelines/memory_tool.py`, `pipelines/usage_stats_tool.py`, `litellm/config.yaml`, `memory-service/`.

---

## 5. Детектор и классификатор

### 5.1. Детектор (rules-only, синхронный)

На вход: `messages` (OpenAI-формат), `files` (из `body["files"]`). На выход: `DetectedInput`.

```python
@dataclass
class DetectedInput:
    has_image: bool
    has_audio: bool
    has_document: bool          # pdf, docx, txt, md
    urls: list[str]             # найденные https?:// ссылки
    lang: Literal["ru","en","other"]
    last_user_text: str
    image_attachments: list[dict]
    audio_attachments: list[dict]
    document_attachments: list[dict]
    wants_image_gen: bool       # по ключам "нарисуй", "generate image"
    wants_web_search: bool      # по ключам "найди в интернете", "поищи"
```

Правила (порядок важен):

| Сигнал | Установка |
|---|---|
| `files[i].type` startswith `image/` или `content[i].type == "image_url"` | `has_image=True`, `image_attachments += ...` |
| `files[i].type` startswith `audio/` | `has_audio=True` |
| `files[i].name` matches `\.(pdf|docx?|txt|md)$` | `has_document=True` |
| regex `https?://\S+` в `last_user_text` | `urls += match` |
| доля кириллицы > 0.3 | `lang = "ru"` |
| regex `(?i)\b(нарисуй\|сгенерируй\s+картинк\|draw\|generate\s+image)\b` | `wants_image_gen=True` |
| regex `(?i)\b(найди\s+в\s+интернете\|поищи\|search\s+the\s+web\|актуальн)\b` | `wants_web_search=True` |

### 5.2. Классификатор (гибрид)

1. **Short-circuit по правилам**: если `has_image` → добавить SubTask(`vision`); если `has_audio` → SubTask(`stt`); если `has_document` → SubTask(`doc_qa`); если `urls` → SubTask(`web_fetch`); если `wants_image_gen` → SubTask(`image_gen`); если `wants_web_search` → SubTask(`web_search`).

2. **LLM-fallback** только для чистого текста без прикреплений и без явных ключей. Один вызов `mws/gpt-oss-20b` с `response_format={"type":"json_object"}`, max_tokens=200:

```json
{
  "intents": ["code" | "math" | "ru_chat" | "general" | "long_doc" | "agentic" | "deep_research" | "presentation"],
  "lang": "ru" | "en" | "other",
  "complexity": "trivial" | "normal" | "hard",
  "primary_model": "mws/...",
  "reason": "<one sentence>"
}
```

3. **Fallback при ошибке JSON**: `mws/t-pro` для RU, `mws/gpt-alpha` для прочего.

### 5.3. Planner

Функция `_plan(detected, classifier_output) -> list[SubTask]`. Правила:

- Каждый "short-circuit" сигнал → отдельный SubTask.
- Чистый текст → SubTask(`ru_chat` | `code` | `reasoner` | `general` | `long_doc`) в зависимости от `primary_model`.
- Если `has_audio` → после `sa_stt` добавить SubTask для транскрипта как текстового запроса (реклассификация).
- Dedup одинаковых kind'ов.
- Лимит: максимум 4 параллельных субагента на один запрос (guard против эксплойтов).

---

## 6. Интерфейсы SubTask / CompactResult

```python
from dataclasses import dataclass, field

@dataclass
class SubTask:
    kind: str                         # vision|stt|code|reasoner|ru_chat|general|long_doc|web_fetch|web_search|image_gen|doc_qa|deep_research|presentation
    input_text: str
    attachments: list[dict] = field(default_factory=list)
    model: str = ""                   # mws/* alias
    max_output_tokens: int = 400
    metadata: dict = field(default_factory=dict)

@dataclass
class CompactResult:
    kind: str
    summary: str                      # ≤ 500 токенов, markdown допустим
    citations: list[str] = field(default_factory=list)  # URL, имена файлов
    artifacts: list[dict] = field(default_factory=list) # {"type":"image","url":"..."}
    error: str | None = None          # если субагент упал
```

---

## 7. Субагенты (13 шт.)

| Имя | Kind | Модель (primary) | Что делает |
|---|---|---|---|
| `sa_general` | `general` | `mws/gpt-alpha` | Универсальный текстовый ответ (EN/mixed). |
| `sa_ru_chat` | `ru_chat` | `mws/t-pro` | Универсальный ответ на русском. |
| `sa_code` | `code` | `mws/qwen3-coder` | Генерация/ревью/дебаг кода. |
| `sa_reasoner` | `reasoner` | `mws/deepseek-r1-32b` | Математика, логика, сложные задачи (с длинным reasoning). |
| `sa_long_doc` | `long_doc` | `mws/glm-4.6` | Длинный контекст (>50K токенов). |
| `sa_vision` | `vision` | `mws/cotype-pro-vl` (RU) / `mws/qwen2.5-vl-72b` (другое) | VLM: описание, OCR, VQA. |
| `sa_stt` | `stt` | `mws/whisper-turbo` | Транскрипция аудио. Результат — `summary = <транскрипт>`, далее реклассификация. |
| `sa_image_gen` | `image_gen` | `mws/qwen-image` | Генерация изображения по промпту. Возвращает `artifacts=[{type:image,url}]`. |
| `sa_web_fetch` | `web_fetch` | `mws/llama-3.1-8b` | Скачать URL, извлечь текст (readability), суммаризовать. |
| `sa_web_search` | `web_search` | `mws/kimi-k2` | DuckDuckGo-поиск + суммаризация топ-3 ссылок. |
| `sa_doc_qa` | `doc_qa` | `mws/glm-4.6` | Q&A по прикреплённым документам через встроенный RAG OpenWebUI. |
| `sa_deep_research` | `deep_research` | — | **Stub**: возвращает `"⚠️ Deep Research будет добавлен в v2"`. |
| `sa_presentation` | `presentation` | — | **Stub**: возвращает `"⚠️ Генерация презентаций будет добавлена в v2"`. |

---

## 8. Маппинг "фича → субагент → модель"

| # | Обязательная фича (xlsx) | Субагент(ы) | Модель |
|---|---|---|---|
| 1 | Текстовый чат | `sa_general` / `sa_ru_chat` | `gpt-alpha` / `t-pro` |
| 2 | Голосовой чат | `sa_stt` → реклассификация | `whisper-turbo` + финальная |
| 3 | Генерация изображений | `sa_image_gen` | `qwen-image` |
| 4 | Аудиофайлы + ASR | `sa_stt` → `sa_general` | `whisper-turbo` + `gpt-alpha` |
| 5 | Изображения (VLM) | `sa_vision` | `cotype-pro-vl` / `qwen2.5-vl-72b` |
| 6 | Файлы и Q&A | `sa_doc_qa` (встроенный RAG) | `glm-4.6` |
| 7 | Поиск в интернете | `sa_web_search` | `kimi-k2` + DuckDuckGo |
| 8 | Веб-парсинг ссылки | `sa_web_fetch` | `llama-3.1-8b` |
| 9 | Долгосрочная память | существующий `memory_function.py` Filter | — |
| 10 | Автовыбор модели | Detector + Classifier + Planner | `gpt-oss-20b` |
| 11 | Ручной выбор модели | штатный OpenWebUI dropdown | любой `mws/*` |
| 12 | Markdown и код | Aggregator system prompt | финальная |

---

## 9. Псевдокод `pipe()`

```python
async def pipe(self, body: dict, __user__=None, __request__=None):
    messages = body.get("messages", [])
    files = body.get("files", [])

    # 1. Детектор (rules)
    detected = self._detect(messages, files)

    # 2. Классификатор + планнер
    plan = await self._classify_and_plan(detected, messages)

    # 3. Collapsible routing decision
    yield self._format_routing_block(plan)

    # 4. Dispatcher — параллельный запуск всех вспомогательных субагентов
    results = await asyncio.gather(
        *[self._run_subagent(task) for task in plan],
        return_exceptions=True,
    )
    compact = [r for r in results if isinstance(r, CompactResult)]

    # 5. Выбор финальной chat-модели
    final_model = "mws/t-pro" if detected.lang == "ru" else "mws/gpt-alpha"

    # 6. Стриминг финального ответа через агрегатор
    async for chunk in self._stream_aggregate(final_model, messages, compact):
        yield chunk
```

`_stream_aggregate` строит системный промпт вида:

```
Ты — финальный агент "MWS GPT Auto". Ниже результаты работы вспомогательных субагентов.
Используй их как факты, не показывай пользователю их "внутреннюю кухню".
Отвечай в markdown на языке пользователя.

[sa_vision] <summary>
[sa_web_fetch] <summary>
...
```

И делает `chat/completions` с `stream=True`, yield'ит delta-chunks.

---

## 10. Интеграция с Memory Service и Langfuse

- **Memory Service** — без изменений. `memory_function.Filter.inlet` работает ДО нашей Pipe-функции, `outlet` — ПОСЛЕ. Проверить в рамках phase-9-12, что inlet корректно добавляет memory system-message даже когда модель — `mws-auto`.
- **Langfuse** — все вызовы LiteLLM автоматически трассируются (`success_callback: ["langfuse"]`). Дополнительно в каждом субагенте передавать `metadata={"trace_id": <parent>, "generation_name": f"sa_{kind}"}` — LiteLLM пробросит в Langfuse и даст иерархическую трассу. Parent trace_id генерируется в начале `pipe()` через `uuid4()`.

---

## 11. Деплой

1. `make up` — поднять стек.
2. Создать admin-аккаунт в OpenWebUI (http://localhost:3000), получить `api_key` из профиля.
3. `echo "OWUI_ADMIN_TOKEN=<key>" >> .env`.
4. `make deploy-functions` — вызывает `scripts/deploy_function.sh`, который делает:

```bash
curl -X POST http://localhost:3000/api/v1/functions/create \
  -H "Authorization: Bearer $OWUI_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d @- <<EOF
{
  "id": "mws_auto_router",
  "name": "MWS GPT Auto",
  "meta": {"description":"Auto-router that picks the best model/tool for every request"},
  "content": "<содержимое auto_router_function.py как JSON-escaped string>"
}
EOF
```

5. В Admin UI → Functions включить переключатель у `mws_auto_router`.
6. `MWS GPT Auto 🎯` появляется в dropdown моделей.

---

## 12. Ручной обход роутера

`pipes()` возвращает ровно один id: `[{"id":"mws-auto","name":"MWS GPT Auto"}]`.

OpenWebUI также подтягивает список моделей от LiteLLM (`/v1/models`), где есть все 26 `mws/*` алиасов. Пользователь может выбрать, например, `mws/qwen3-coder` — запрос уходит напрямую в LiteLLM, Pipe-функция не задевается. Никакого дополнительного кода не требуется — достаточно документации в `README_proj.md`.

---

## 13. End-to-end верификация (12 сценариев)

После `make up` + `make deploy-functions`, открыть http://localhost:3000, выбрать модель "MWS GPT Auto":

| # | Сценарий | Ожидание |
|---|---|---|
| 1 | Текст RU: "Расскажи про МТС" | `sa_ru_chat` → `t-pro`. Markdown. |
| 2 | Голосовой ввод через микрофон (RU) | `sa_stt` → `sa_ru_chat`. |
| 3 | "Нарисуй логотип кота с очками" | `sa_image_gen` → картинка в чате. |
| 4 | Загрузить `.mp3` + "сделай протокол" | `sa_stt` + `sa_general`. |
| 5 | Загрузить фото договора | `sa_vision` → `cotype-pro-vl`. OCR. |
| 6 | Загрузить PDF + "summarize chapter 2" | `sa_doc_qa` → `glm-4.6`. |
| 7 | "Что нового в Qwen 3 сегодня?" | `sa_web_search` → `kimi-k2`. |
| 8 | "https://example.com/news — что тут?" | `sa_web_fetch` → `llama-3.1-8b`. |
| 9 | (следующий день) "как меня зовут?" | memory_function отдаёт контекст. |
| 10 | "напиши fibonacci на rust" | classifier → `qwen3-coder`. |
| 11 | Вручную выбрать `mws/deepseek-r1-32b`, задать задачу | Pipe НЕ вызывается, прямая трасса в Langfuse. |
| 12 | "Сделай таблицу со сравнением Python и Rust" | Markdown таблица. |

Успех = все 12 работают, и в Langfuse видна иерархическая трасса с parent `mws-auto` и детьми `sa_*` (для шагов 1-10, 12), и прямая трасса без parent'а для шага 11.

---

## 14. Декомпозиция на phase-9 задачи

См. `tasks/phase-9-1..12-*.md`. Задачи упорядочены по зависимостям; 9-1, 9-2, 9-3 делаются последовательно, далее 9-4 открывает параллельные 9-5/6/7/8/11, затем 9-9 собирает всё, 9-10 автоматизирует деплой, 9-12 проводит верификацию.
