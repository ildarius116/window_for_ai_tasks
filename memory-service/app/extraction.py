import json
import logging

import httpx

from app.config import EXTRACTION_MODEL, LITELLM_API_KEY, LITELLM_URL

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Из следующего диалога извлеки важные факты о пользователе:
- Предпочтения (языки программирования, фреймворки, стиль работы)
- Контекст работы (проект, роль, технологии)
- Явные просьбы ("всегда делай X", "я предпочитаю Y")
- Ключевые договорённости

Верни ТОЛЬКО валидный JSON: {"memories": ["факт 1", "факт 2"]}
Только реально важные факты. Максимум 5 за разговор.
Если важных фактов нет — верни {"memories": []}"""


async def extract_memories(messages: list[dict]) -> list[str]:
    """Extract memorable facts from a conversation using LLM."""
    conversation = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in messages
        if m.get("content")
    )

    if not conversation.strip():
        return []

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json={
                    "model": EXTRACTION_MODEL,
                    "messages": [
                        {"role": "system", "content": EXTRACTION_PROMPT},
                        {"role": "user", "content": conversation},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1024,
                },
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # Parse JSON from response (handle markdown code blocks)
            content = content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1].rsplit("```", 1)[0]

            result = json.loads(content)
            return result.get("memories", [])

    except Exception as e:
        logger.error("Memory extraction failed: %s", e)
        return []
