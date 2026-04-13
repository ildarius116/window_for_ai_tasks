# phase-11-8 — Классификатор: presentation побеждает doc_qa/long_doc

## Цель
Сейчас при запросе «сделай презентацию из этого PDF» LLM-классификатор уводит в `long_doc`/`doc_qa`, потому что длинный документ перевешивает. Нужно поднять приоритет `presentation`, **не** ломая правило «regex не является первичным гейтом для семантической маршрутизации» (см. feedback memory).

## Что сделать
- В `_llm_classify` system prompt (auto_router_function.py ~678):
  - Добавить явный блок **CRITICAL RULE — presentation** до блока memory_recall.
  - Правила: «Если пользователь просит создать презентацию, слайды, pptx, powerpoint, представление — ВСЕГДА `intent="presentation"`, даже если приложен документ. `doc_qa`/`long_doc` в этом случае НЕ добавлять».
  - Два примера:
    1. User: «Сделай презентацию про Python async/await» → `{"intents":["presentation"], ...}`.
    2. User: «Вот резюме. Сделай из него презентацию.» + has_document=true → `{"intents":["presentation"], ...}`.
- В `_classify_and_plan` после вызова `_llm_classify` добавить safety-net (НЕ первичный гейт):
  ```python
  _PPTX_MARKERS = {
      "презентация","презентацию","презентацией","презентации",
      "слайды","слайдов","слайд","pptx","powerpoint","power point",
      "представление","presentation","slides","slide deck","deck",
  }
  def _looks_like_presentation(text: str) -> bool:
      t = text.lower()
      return any(m in t for m in _PPTX_MARKERS)
  ```
  и в конце `_classify_and_plan` (после того, как LLM уже отработал):
  ```python
  if _looks_like_presentation(detected.last_user_text):
      # override любой план на одиночный presentation
      plan = [SubTask(
          kind="presentation",
          input_text=detected.last_user_text,
          model=None,  # pptx-service сам решит
          attachments=list(detected.document_attachments or []),
          metadata={"trace_id": trace_id, "user_id": user_id},
      )]
  ```
- Убедиться, что safety-net НЕ срабатывает на слова в середине текста без явного намерения (например, «презентация компании прошла хорошо» — срабатывает, это ок для v1; если станет проблемой — перейти на подстроки с границами слов, но это не сейчас).

## Критерии готовности
- Smoke:
  - «Сделай из него презентацию» + PDF → plan = `[presentation]`, НЕТ doc_qa.
  - «Сделай презентацию про async/await на 5 слайдов» → plan = `[presentation]`.
  - «Что написано в этом резюме?» + PDF → plan содержит `doc_qa`, НЕ presentation (safety-net не сработал).
  - «Переведи этот документ на английский» + PDF → plan содержит `translate`/`doc_qa`, НЕ presentation.
- Все существующие smoke-группы A/B/C/E продолжают проходить (regression).

## Затронутые файлы
- `pipelines/auto_router_function.py`

## Зависит от
- phase-11-7 (иначе override будет бессмысленным — стаб вернёт заглушку)
