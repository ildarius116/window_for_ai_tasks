# phase-11-10 — Документация: CLAUDE.md + phase-done

## Цель
Обновить канонические файлы документации после успешного прохождения phase-11-9.

## Что сделать
- `CLAUDE.md`:
  - Architecture diagram — добавить `pptx-service (:8000 internal)` рядом с `tts-service`.
  - Раздел «Services» — поднять счётчик с 11 до 12, упомянуть `pptx-service`.
  - «Commands» — добавить `docker compose build pptx-service && docker compose up -d pptx-service`.
  - «Development Conventions» — короткий абзац про `pptx-service`: что он парсит PDF/DOCX/TXT, зовёт LiteLLM для JSON-схемы, рендерит через python-pptx, файлы доставляются через OpenWebUI Files API (`OWUI_ADMIN_TOKEN` обязателен для доставки; без него сабагент деградирует до markdown-плана).
  - «Key Files» — `pptx-service/main.py`, `pptx-service/builder.py`, `pptx-service/schema_llm.py`, `pptx-service/parsing.py`.
  - «Project Status» — новый блок:
    > **Phase 11 — Presentations (done, YYYY-MM-DD):** ... краткий отчёт по фазе, ссылка на `tasks_done/phase-11-done.md`.
- `tasks_done/phase-11-done.md` — финальный отчёт:
  - Что сделано (по задачам phase-11-1..10).
  - Smoke-результаты из phase-11-9.
  - Известные ограничения: нет корпоративного шаблона, нет изображений в слайдах, fallback-ветка при отсутствующем токене, лимит 20 МБ.
  - Что осталось на v2: mws_template.pptx, автогенерация обложки через qwen-image, поддержка markdown-файлов как источника с preserve-структурой.
- `.env.example` — убедиться, что `OWUI_ADMIN_TOKEN=` присутствует с комментарием «required for phase-11 pptx delivery».

## Критерии готовности
- `CLAUDE.md` отражает актуальное состояние стека (12 сервисов, phase-11 done).
- `tasks_done/phase-11-done.md` создан и ссылается на все 10 задач.

## Затронутые файлы
- `CLAUDE.md`
- `tasks_done/phase-11-done.md` (new)
- `.env.example`

## Зависит от
- phase-11-9 (E2E должны пройти перед финализацией доков)
