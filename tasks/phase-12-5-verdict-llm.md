# phase-12-5 — LLM-вердикт по утверждениям

## Цель
Реализовать `_verdict_claims(claims, url_statuses, user_question) -> list[Claim]` — финальный LLM-шаг фактчекера, который ставит каждому claim один из вердиктов `proven | plausible | fabricated` на основании содержимого реально доступных URL.

## Что сделать
- В `pipelines/auto_router_function.py` в классе `Pipe` добавить:
  ```python
  _VERDICT_PROMPT = """Ты — проверяющий факты. Для каждого утверждения определи вердикт
  на основании СТРОГО предоставленных доказательств (snippets реальных URL).

  Правила:
  - proven: утверждение явно подтверждается текстом одного из snippets;
            ОБЯЗАТЕЛЬНО укажи evidence_url из списка доказательств.
  - plausible: прямого подтверждения в snippets нет, но утверждение не противоречит
               известным фактам и выглядит правдоподобно. evidence_url пустой.
  - fabricated: утверждение противоречит snippet'ам ИЛИ ссылается на URL со статусом
                url_404 / url_unreachable ИЛИ явно выдумано.

  ЗАПРЕЩЕНО:
  - Ставить proven без evidence_url из списка доказательств.
  - Ссылаться на знания, которых нет в snippets.

  Верни JSON: {"verdicts": [{"claim": "...", "verdict": "...", "evidence_url": "...", "reason": "..."}]}
  """

  async def _verdict_claims(
      self,
      claims: list[Claim],
      url_statuses: list[UrlStatus],
      user_question: str,
  ) -> list[Claim]:
      if not claims:
          return []

      # Готовим доказательную базу. Только url_ok и url_redirect → finalUrl.
      evidence_lines: list[str] = []
      url_ok_set: set[str] = set()
      for us in url_statuses:
          if us.status in ("url_ok", "url_redirect") and us.snippet:
              evidence_lines.append(f"URL: {us.final_url or us.url}\nSnippet: {us.snippet[:1500]}")
              url_ok_set.add(us.final_url or us.url)
          elif us.status in ("url_404", "url_unreachable"):
              evidence_lines.append(f"URL: {us.url} — НЕДОСТУПЕН ({us.status})")
      evidence_text = "\n\n".join(evidence_lines) if evidence_lines else "(нет доказательств)"

      claims_text = "\n".join(f"- {c.text}" for c in claims)
      user_msg = (
          f"Вопрос пользователя: {user_question[:500]}\n\n"
          f"Утверждения для проверки:\n{claims_text}\n\n"
          f"Доказательства:\n{evidence_text}"
      )

      body = {
          "model": self.valves.fact_check_model,
          "messages": [
              {"role": "system", "content": _VERDICT_PROMPT},
              {"role": "user", "content": user_msg},
          ],
          "temperature": 0.0,
          "max_tokens": 900,
          "response_format": {"type": "json_object"},
      }
      try:
          data = await self._litellm_json(body, timeout=10.0)
      except Exception as e:
          print(f"fact_check verdict FAILED: {type(e).__name__}: {e}")
          return [
              Claim(text=c.text, source_kind=c.source_kind, verdict="unknown",
                    reason="verdict_llm_failed")
              for c in claims
          ]

      verdicts = data.get("verdicts") or []
      by_text: dict[str, dict] = {
          str(v.get("claim", "")).strip(): v for v in verdicts if isinstance(v, dict)
      }

      out: list[Claim] = []
      for c in claims:
          v = by_text.get(c.text, {})
          verdict = str(v.get("verdict", "unknown")).lower().strip()
          if verdict not in ("proven", "plausible", "fabricated"):
              verdict = "unknown"
          ev_url = str(v.get("evidence_url", "")).strip() or None
          # Защита от галлюцинации proven: evidence_url должен быть из доказательной базы
          if verdict == "proven" and (not ev_url or ev_url not in url_ok_set):
              verdict = "plausible"
              ev_url = None
          out.append(Claim(
              text=c.text,
              source_kind=c.source_kind,
              verdict=verdict,
              evidence_url=ev_url,
              reason=str(v.get("reason", "")).strip()[:300],
          ))
      return out
  ```

- Проверка `evidence_url ∈ url_ok_set` — критичная защита: без неё LLM может поставить `proven` и сослаться на выдуманный URL.
- При пустом списке доказательств все claims переводятся в `plausible` (они не могут быть ни proven, ни fabricated, пока не проверены независимо).

## Критерии готовности
- На входе 3 claim'а + 2 url_ok snippet'а из rbc.ru и wikipedia.org:
  - Claims, которые есть в snippet'ах → `proven` + evidence_url.
  - Claims без подтверждения → `plausible`.
- На входе `url_404` в статусах и claim, ссылающийся на этот URL → `fabricated`.
- LLM-фейл → возврат `unknown` у всех claims, пайп не падает.

## Затронутые файлы
- `pipelines/auto_router_function.py`
