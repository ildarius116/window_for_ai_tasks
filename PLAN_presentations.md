# Phase 11 — Генерация реальных .pptx презентаций


## Context

Сейчас сабагент `_sa_presentation` в `pipelines/auto_router_function.py:1670` — это
v1-заглушка: возвращает сообщение «будет добавлено в v2». Когда пользователь
просит «сделай презентацию из резюме», авто-роутер в лучшем случае отвечает
текстовой структурой слайдов, которую нужно вручную копировать в PowerPoint.

Цель Phase 11 — научить стек генерировать **реальный `.pptx`-файл** и отдавать
его пользователю ссылкой в чате, по той же схеме, что уже работает для
изображений (`_sa_image_gen` → `artifacts` → `_render_artifacts` → markdown).

Выбранный способ доставки — **вариант 3.a** из обсуждения: микросервис собирает
`.pptx`, пайп загружает его в OpenWebUI через `POST /api/v1/files/` и
возвращает в ответе markdown-ссылку на `/api/v1/files/{id}/content`.
Вариант 3.b (volume + nginx) отклонён.

## Архитектурное решение

Делаем отдельный микросервис `pptx-service` (а не расширяем `memory-service`),
по тем же причинам, по которым TTS вынесен в `tts-service`: отдельный набор
зависимостей (`python-pptx`, возможно `Pillow`), независимый деплой, не
раздувает memory-service тяжёлыми библиотеками.

Поток запроса:

```
User: "Сделай презентацию из резюме" + PDF
  ↓
memory_function.inlet → _inject_file_tags  (уже есть)
  → <mws_doc_files>[{id, filename, path, content_type}]</mws_doc_files>
  ↓
auto_router_function.Pipe._detect      (уже есть: has_document=True)
  ↓
_classify_and_plan → intent="presentation"
  ↓                   (нужно поднять приоритет над doc_qa/long_doc
  ↓                    при явных словах "презентация/слайды/pptx")
_sa_presentation (переписан):
  1. Читает исходник (если doc.path доступен на диске — извлекает текст
     через python-pptx/PyPDF2/docx2txt; если нет — использует last_user_text)
  2. LLM-шаг A: mws/glm-4.6, response_format=json_object,
     возвращает {title, subtitle, slides:[{title, bullets[], notes}]}
  3. POST http://pptx-service:8000/build {schema, style:"mws"} → bytes .pptx
  4. POST http://openwebui:8080/api/v1/files/  (multipart, Bearer OWUI_ADMIN_TOKEN)
     → {id, filename, ...}
  5. return CompactResult(
         kind="presentation",
         summary="Готова презентация «{title}» — {N} слайдов",
         artifacts=[{"type":"file","url":f"/api/v1/files/{id}/content",
                     "filename":"...pptx","mime":"application/vnd...pptx"}]
     )
  ↓
_stream_aggregate (has_artifacts=True → буферизация + strip markdown images,
   но нужно расширить strip на любые markdown-ссылки от модели,
   чтобы она не «галлюцинировала» ссылку на .pptx)
  ↓
_render_artifacts (расширить — добавить ветку type="file"):
   out.append(f"\n📎 [{filename}]({url})")
```

## Компоненты и правки

### 1. Новый микросервис `pptx-service/`
Структура — минимальная FastAPI-сервисы, по образцу `tts-service/`:

- `pptx-service/Dockerfile` — `python:3.11-slim`, `pip install python-pptx fastapi uvicorn pydantic`.
- `pptx-service/main.py` — один endpoint:
  - `POST /build` — тело: `PresentationSchema` (pydantic). Ответ: `application/vnd.openxmlformats-officedocument.presentationml.presentation` (bytes) через `StreamingResponse`.
  - `GET /health` — для healthcheck.
- `pptx-service/builder.py` — функция `build_pptx(schema) -> bytes`:
  - Титульный слайд (layout 0): `title`, `subtitle`.
  - На каждый `slide` — layout 1 (Title + Content): title + буллеты, `notes_slide.notes_text_frame.text = slide.notes`.
  - Опционально: загружать шаблон `mws_template.pptx` (если положен рядом) — `Presentation("mws_template.pptx")`. Для MVP — дефолтный шаблон python-pptx.
  - Возвращать через `BytesIO`.

Схема запроса:
```python
class Slide(BaseModel):
    title: str
    bullets: list[str] = []
    notes: str | None = None

class PresentationSchema(BaseModel):
    title: str
    subtitle: str | None = None
    slides: list[Slide]
    style: str = "mws"  # зарезервировано под темизацию
```

### 2. `docker-compose.yml`
- Добавить сервис `pptx-service` (build, healthcheck, `mws-network`, без `ports` — только внутренний).
- В сервис `openwebui` добавить `OWUI_ADMIN_TOKEN: ${OWUI_ADMIN_TOKEN}` — без него пайп не сможет вызвать Files API. Уже присутствует в `.env.example`, но сейчас не пробрасывается в контейнер.
- В `Makefile` — `make build` должен собирать и `pptx-service`.

### 3. `pipelines/auto_router_function.py`

**3.1 `_render_artifacts` (auto_router_function.py:1103)** — расширить:
```python
def _render_artifacts(self, results):
    out = []
    for r in results:
        for art in r.artifacts or []:
            t = art.get("type")
            if t == "image" and art.get("url"):
                out.append(f"![generated]({art['url']})")
            elif t == "file" and art.get("url"):
                name = art.get("filename") or "file"
                out.append(f"📎 [{name}]({art['url']})")
    return "\n".join(out)
```

**3.2 `_stream_aggregate` (строка ~174)** — текущая регулярка
`r"!\[[^\]]*\]\([^\)]+\)"` срезает только markdown-картинки. Нужно срезать
и обычные markdown-ссылки, если они указывают на `.pptx`/`/api/v1/files/`,
чтобы агрегатор не продублировал наш артефакт. Минимально — добавить
вторую регулярку для ссылок на файлы; оставить обычные ссылки в тексте.

**3.3 Classifier system prompt (строка ~678)** — добавить явный пример
для presentation с документом во вложении и правило «если в сообщении есть
слова *презентация/слайды/pptx/powerpoint/представление* — intent всегда
`presentation`, даже если есть `has_document=True`». Это нужно, потому что
сейчас длинный PDF уводит в `long_doc`/`doc_qa`.

**3.4 `_classify_and_plan`** — после LLM-классификатора добавить safety-net
(по аналогии с `_looks_like_memory_recall`): словарный триггер
`_PPTX_MARKERS = {"презентация", "презентацию", "слайды", "pptx",
"powerpoint", "презентацией", "presentation", "slides"}`. Если триггер
сработал и есть `has_document`, то plan = `[SubTask(kind="presentation",
attachments=detected.document_attachments)]`, а `doc_qa`/`long_doc` не
добавляются. Это не «regex как первичный гейт» (см. feedback-memory), а
safety-net ПОСЛЕ LLM-классификатора.

**3.5 `_sa_presentation` (полная переработка)** — логика описана выше в
секции «Поток запроса». Ключевые моменты:
- LLM-схема: `response_format={"type":"json_object"}`, system-prompt с
  примером и строгими правилами («не более 10 слайдов», «каждый буллет
  ≤ 120 символов», «notes — 1–3 предложения», «title — ≤ 8 слов»).
  Модель по умолчанию — `mws/glm-4.6` (длинный контекст под резюме/PDF).
- Чтение исходника: если `task.attachments[0]["path"]` существует на
  диске (путь внутри контейнера `openwebui`, т.к. `_sa_stt` делает то же
  самое) — открыть файл и извлечь текст. Для PDF использовать уже
  установленный в openwebui `pypdf`/`PyPDF2` (если нет — добавить в
  requirements самого пайпа НЕЛЬЗЯ, пайп работает в контейнере owui;
  поэтому парсинг PDF делаем на стороне `pptx-service`, передавая в
  `/build` сырое тело или путь). **Альтернатива:** передать в pptx-service
  исходный текст документа из уже извлечённого OpenWebUI RAG-контекста —
  но его пайп не видит. Решение: **пайп отправляет в pptx-service сам
  бинарь PDF через multipart**, pptx-service парсит его, генерирует
  структуру через тот же LiteLLM, и возвращает готовый .pptx. Таким
  образом весь pipeline парсинга/LLM-схемы/сборки живёт в одном месте.

**ВАЖНОЕ УТОЧНЕНИЕ АРХИТЕКТУРЫ** (приоритетный вариант):
перенести LLM-шаг генерации JSON-схемы **внутрь pptx-service**, а пайп
делает один вызов `POST /build` (multipart: source file + `user_instruction`
поля). pptx-service:
1. Парсит PDF/DOCX/TXT → текст.
2. Вызывает LiteLLM (`mws/glm-4.6`) с `response_format=json_object`.
3. Рендерит .pptx через `python-pptx`.
4. Отдаёт bytes.

Это делает сервис самодостаточным и снимает с пайпа ответственность за
парсинг файлов. Пайп после ответа только заливает bytes в Files API.

### 4. Загрузка в OpenWebUI Files API
- Helper `_upload_to_owui_files(self, content: bytes, filename: str) -> str`
  в `auto_router_function.py`:
  - URL: `http://localhost:8080/api/v1/files/` (пайп крутится внутри owui
    контейнера, так что loopback — надёжнее).
  - Headers: `Authorization: Bearer {os.getenv('OWUI_ADMIN_TOKEN')}`.
  - Multipart с полем `file`.
  - При ошибке/отсутствии токена — вернуть `CompactResult(error=...)`,
    сабагент деградирует до текстовой структуры (fallback).
- Если вернулся `id` — собрать URL `/api/v1/files/{id}/content` и положить
  в `artifacts`.

### 5. `bootstrap.py` (опционально для MVP)
Можно оставить без изменений. Если в будущем захотим ноль-конфиг:
бутстрап-сайдкар может минтить `OWUI_ADMIN_TOKEN` из БД `openwebui` после
первого сигнапа и писать его в shared env. Но это отдельная задача и не
блокирует Phase 11.

### 6. `pptx-service/requirements.txt`
```
fastapi==0.115.6
uvicorn[standard]==0.34.0
pydantic==2.10.3
httpx==0.28.1
python-pptx==1.0.2
pypdf==5.1.0
python-docx==1.1.2
```

### 7. `CLAUDE.md`
- Обновить Architecture diagram — добавить `pptx-service`.
- Раздел «Services» — `(11 → 12 total)`.
- Раздел «Key Files» — `pptx-service/main.py`.
- В Phase-Status добавить пункт «Phase 11 — Presentations (done, YYYY-MM-DD)».

## Декомпозиция на задачи (будущие `tasks/phase-11-*.md`)

1. **phase-11-1-pptx-service-skeleton.md** — создать `pptx-service/` (Dockerfile,
   main.py с `/health` и заглушкой `/build`), добавить в `docker-compose.yml`,
   прогнать `docker compose up -d pptx-service`, проверить health.
2. **phase-11-2-pptx-builder.md** — `builder.py` с `build_pptx(schema)`, юнит-
   тест рендера на фикстуре (3 слайда), возвращает валидный `.pptx`,
   открывается в LibreOffice без ошибок.
3. **phase-11-3-pptx-llm-schema.md** — внутри pptx-service: парсер PDF/DOCX/TXT
   + вызов LiteLLM (`mws/glm-4.6`, JSON mode) для построения `PresentationSchema`
   из текста документа. System prompt + few-shot пример.
4. **phase-11-4-pptx-build-endpoint.md** — финальный `POST /build` (multipart:
   file + user_instruction): парсинг → LLM → builder → StreamingResponse.
   Error handling: 400 на пустой файл, 502 на LiteLLM failure, 413 на файл > 20 МБ.
5. **phase-11-5-owui-files-upload-helper.md** — `_upload_to_owui_files` helper
   в `auto_router_function.py` + проброс `OWUI_ADMIN_TOKEN` в env openwebui в
   compose. Юнит-проверка через curl внутри контейнера.
6. **phase-11-6-render-artifacts-file.md** — расширить `_render_artifacts` на
   `type="file"` + обновить `_stream_aggregate` чтобы стрип покрывал ссылки
   на наш артефакт. Не ломать существующий image-путь.
7. **phase-11-7-sa-presentation-rewrite.md** — переписать `_sa_presentation`:
   подготовить bytes источника → `POST pptx-service/build` → upload в Files
   API → вернуть `CompactResult` с file-artifact. Fallback на текстовую
   структуру при ошибках.
8. **phase-11-8-classifier-presentation-intent.md** — обновить system prompt
   LLM-классификатора (пример для «презентация + документ») + `_PPTX_MARKERS`
   safety-net после `_llm_classify`. Гарантировать, что `presentation`
   побеждает `doc_qa`/`long_doc` при явных словах про слайды.
9. **phase-11-9-e2e-verification.md** — smoke-test:
   (a) «Сделай презентацию про Python» (без документа) — генерирует из
   знаний модели;
   (b) «Сделай презентацию из резюме» + PDF — парсит PDF, 6–8 слайдов,
   ссылка кликается, файл открывается в PowerPoint/LibreOffice и
   содержит ожидаемые секции;
   (c) ручной выбор модели (не Auto 🎯) — не ломается, роутер bypassed;
   (d) ошибка LiteLLM во время генерации схемы → сабагент возвращает
   текстовый fallback, а не 500.
10. **phase-11-10-docs-claude-md.md** — обновить `CLAUDE.md` (архитектура,
    сервисы, файлы, Phase-Status) и написать `tasks_done/phase-11-done.md`.

## Критические файлы

- `pipelines/auto_router_function.py` — `_sa_presentation`, `_render_artifacts`,
  `_stream_aggregate`, `_classify_and_plan`, classifier system prompt.
- `docker-compose.yml` — новый сервис + `OWUI_ADMIN_TOKEN` в openwebui env.
- `pptx-service/` — новая директория, весь микросервис.
- `Makefile` — `build` target.
- `CLAUDE.md` — архитектура и Phase-Status.

## Переиспользуемые существующие элементы

- `CompactResult.artifacts` + `_render_artifacts` — тот же контракт, что и у
  `_sa_image_gen`.
- `_stream_aggregate` буферизация при `has_artifacts` — уже делает strip
  markdown, нужно чуть расширить.
- `httpx.AsyncClient` + `_auth_headers()` — повторить паттерн из `_sa_image_gen`.
- Парсинг multipart в pptx-service — тот же паттерн, что `_sa_stt` использует
  для `/v1/audio/transcriptions`.
- memory_function inlet уже инжектит `<mws_doc_files>` с `path` — `_detect` их
  подхватывает в `document_attachments`, менять не нужно.

## Верификация (end-to-end)

1. `docker compose up -d --build pptx-service openwebui` — оба healthy.
2. `curl http://localhost:8080/api/v1/files/ -H "Authorization: Bearer $OWUI_ADMIN_TOKEN"` — 200 (проверка токена).
3. В OpenWebUI: выбрать «MWS GPT Auto 🎯», приложить `resume.pdf`, написать
   «Сделай из него презентацию». Ожидаем:
   - В логах owui: `classify → presentation`, `sa_presentation → upload ok`.
   - В чате: короткая подводка + ссылка `📎 [resume_presentation.pptx](...)`.
   - Клик по ссылке → скачивается файл, открывается в LibreOffice, видны
     титульный слайд + 5–8 слайдов с буллетами из резюме.
4. Повторить без вложения: «Сделай презентацию про Python async/await на 5
   слайдов» — модель генерирует из своих знаний, файл приходит.
5. Симулировать падение LiteLLM (выключить на секунду) — сабагент возвращает
   текстовую структуру, в чате видим markdown-слайды, без 500.
6. Ручной выбор `mws/qwen3-235b` из dropdown — обычный чат, pptx-тракт не
   триггерится.

## Риски и открытые вопросы

- **`OWUI_ADMIN_TOKEN`** — кто его генерирует и обновляет? Сейчас это ручной
  шаг оператора после первого сигнапа. Для MVP — принимаем, что оператор
  кладёт его в `.env`. Долгосрочно — бутстрап-сайдкар может минтить сам.
- **Размер PDF** — ставим лимит 20 МБ на `/build`, чтобы не блокировать
  сервис на огромных файлах.
- **Форматирование буллетов** — первая итерация без стилей, только буллеты +
  заголовок. Корпоративный шаблон `mws_template.pptx` — вторая итерация.
- **Изображения внутри презентации** — пока НЕТ. Если нужно, отдельная
  задача: шаг «сгенерировать обложку через `qwen-image` → вставить на титульный
  слайд».

## Что делать сейчас

Выйти из plan-mode и дождаться одобрения. После одобрения:
1. Скопировать этот файл в `PLAN_presentations.md` в корне проекта.
2. Создать 10 файлов `tasks/phase-11-*.md` по списку выше, каждый — с краткой
   целью, критериями готовности и затронутыми файлами.
3. Дальше смотрим, запускаем или корректируем.