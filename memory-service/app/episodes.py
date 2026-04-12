import logging

import httpx

from app.config import LITELLM_API_KEY, LITELLM_URL, SUMMARY_MODEL

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "Summarize the conversation in 1-2 sentences. "
    "Plain text, no preamble, no bullets. "
    "Focus on topic and user intent."
)


class SummaryError(RuntimeError):
    pass


async def generate_summary(messages: list[dict]) -> str:
    """Generate a short 1-2 sentence summary of a conversation window."""
    conversation = "\n".join(
        f"{m.get('role', 'user')}: {m.get('content', '')}"
        for m in messages
        if m.get("content")
    )

    if not conversation.strip():
        raise SummaryError("No non-empty messages to summarize")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json={
                    "model": SUMMARY_MODEL,
                    "messages": [
                        {"role": "system", "content": SUMMARY_PROMPT},
                        {"role": "user", "content": conversation},
                    ],
                    "temperature": 0.1,
                    "max_tokens": 120,
                },
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
    except Exception as e:
        raise SummaryError(f"LiteLLM unavailable: {e}") from e

    if resp.status_code != 200:
        raise SummaryError(
            f"LiteLLM /v1/chat/completions returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    content = (data["choices"][0]["message"]["content"] or "").strip()
    if not content:
        raise SummaryError("LiteLLM returned empty summary")
    return content
