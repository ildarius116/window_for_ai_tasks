# phase-12-4 — LLM-извлечение проверяемых утверждений

## Цель
Реализовать `_extract_claims(results, user_question) -> list[Claim]` — LLM-шаг, выделяющий из `CompactResult.summary` других субагентов до 6 конкретных, проверяемых утверждений (факты про людей, события, даты, числа, цитаты).

## Что сделать
- В `pipelines/auto_router_function.py` в классе `Pipe` добавить:
  ```python
  _CLAIM_EXTRACT_PROMPT = """Ты — выделитель проверяемых фактов.
  На вход — сводки ответов от других AI-субагентов. Выдели утверждения,
  которые МОЖНО проверить по внешним источникам (люди, организации,
  события, даты, числа, адреса, URL, цитаты).

  ЗАПРЕЩЕНО включать:
  - общие мнения ("это важная тема")
  - перефразирование вопроса пользователя
  - утверждения без конкретики

  Верни JSON: {"claims": [{"text": "...", "source_kind": "..."}]}
  Не более 6 claims. Формулируй каждый claim одним полным предложением.
  """

  async def _extract_claims(
      self,
      results: list[CompactResult],
      user_question: str,
  ) -> list[Claim]:
      checkable = [r for r in results if r.kind in _CHECKABLE_KINDS and not r.error and r.summary]
      if not checkable:
          return []
      lines = [f"Вопрос пользователя: {user_question[:500]}"]
      for r in checkable:
          lines.append(f"--- {r.kind} ---\n{r.summary[:1500]}")
      user_msg = "\n\n".join(lines)

      body = {
          "model": self.valves.fact_check_claim_model,
          "messages": [
              {"role": "system", "content": _CLAIM_EXTRACT_PROMPT},
              {"role": "user", "content": user_msg},
          ],
          "temperature": 0.0,
          "max_tokens": 600,
          "response_format": {"type": "json_object"},
      }
      try:
          data = await self._litellm_json(body, timeout=8.0)
      except Exception as e:
          print(f"fact_check claim_extract FAILED: {type(e).__name__}: {e}")
          return []
      raw = (data.get("claims") or [])[: self.valves.fact_check_max_claims]
      out: list[Claim] = []
      for item in raw:
          if not isinstance(item, dict):
              continue
          text = str(item.get("text", "")).strip()
          if len(text) < 10:
              continue
          src = str(item.get("source_kind", "")).strip() or "web_search"
          out.append(Claim(text=text, source_kind=src))
      return out
  ```
- Использовать существующий `_litellm_json` helper (если нет — создать тонкую обёртку вокруг httpx + JSON-parse + один retry на `JSONDecodeError`). Поискать в файле — там уже есть похожий паттерн в `_llm_classify`.
- Не использовать regex/правила для извлечения — только LLM. Регексы плохо отличают факт от мнения.
- Температура ровно `0.0`, detail логи — только при `valves.debug`.

## Критерии готовности
- На входе «Мисс Мира 2025» из ответа deep_research должны быть извлечены claims уровня:
  - «Opal Suchata Chuangsri выиграла Miss World 2025»,
  - «Финал прошёл 31 мая 2025 года в Хайдарабаде»,
  - «Корону вручила Кристина Пышкова».
- Не извлекаются общие фразы («проект повышает осведомлённость о женском здоровье»).
- При пустом списке checkable результат `[]`, без LLM-вызова.
- При ошибке LiteLLM — возвращает `[]`, печатает лог, не ломает пайп.

## Затронутые файлы
- `pipelines/auto_router_function.py`
