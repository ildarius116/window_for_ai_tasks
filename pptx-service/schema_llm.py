from __future__ import annotations

import json
import logging
import os
from typing import Optional

import httpx
from pydantic import ValidationError

from models import PresentationSchema

log = logging.getLogger("pptx-service.schema_llm")

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
SCHEMA_MODEL = os.getenv("SCHEMA_MODEL", "mws/glm-4.6")
REQUEST_TIMEOUT = int(os.getenv("SCHEMA_TIMEOUT", "180"))


class SchemaGenerationError(RuntimeError):
    pass


SYSTEM_PROMPT = (
    "You are a slide deck architect. From a source text and a user instruction, "
    "produce a JSON object with the EXACT schema:\n"
    '{"title": "<str>", "subtitle": "<str|null>", '
    '"cover_image_prompt": "<str|null>", '
    '"slides": [{"title": "<str>", "bullets": ["<str>", ...], '
    '"notes": "<str|null>", "image_prompt": "<str|null>"}]}\n\n'
    "STRICT RULES:\n"
    "- Return ONLY the JSON object, no prose, no markdown fences.\n"
    "- Keep the user's language (Russian stays Russian, English stays English) "
    "for title/subtitle/bullets/notes.\n"
    "- 5–10 content slides max (plus the title slide).\n"
    "- Each slide title ≤ 8 words, no trailing punctuation.\n"
    "- 3–6 bullets per slide, each bullet ≤ 120 characters, short and informative.\n"
    "- `notes` — 1–3 sentences of speaker notes per slide, optional.\n"
    "- `subtitle` — short tagline or author name if available, else null.\n"
    "- `cover_image_prompt` — a concise ENGLISH prompt (≤ 25 words) describing "
    "a photorealistic hero image for the title slide. Style cue: "
    "'photorealistic, cinematic lighting, 16:9, no text, no watermark'. "
    "Avoid text/logos/people faces. Example for a deck about elephants: "
    "'majestic african elephant herd walking across savanna at golden sunset, "
    "photorealistic, cinematic lighting, 16:9'. Return null only if the topic "
    "is inherently visual-free (e.g. pure math proof).\n"
    "- `image_prompt` per slide — also ENGLISH, ≤ 20 words, focused on the "
    "slide topic, same style cue. Return null only when the slide is a pure "
    "code listing or abstract formula.\n\n"
    "EXAMPLE (abbreviated):\n"
    '{"title":"Резюме — Иван Петров",'
    '"subtitle":"Python Developer",'
    '"cover_image_prompt":"modern developer workspace with laptop, clean minimal desk, soft natural light, photorealistic, 16:9",'
    '"slides":['
    '{"title":"Опыт","bullets":["5 лет Python","FastAPI / Django","Async I/O"],'
    '"notes":"Кратко о ключевых ролях.",'
    '"image_prompt":"programmer hands typing on laptop keyboard, close-up, cinematic, 16:9"},'
    '{"title":"Стек","bullets":["PostgreSQL","Redis","Docker"],"notes":null,'
    '"image_prompt":"abstract technology stack, database and container icons, blue gradient, 16:9"}'
    "]}"
)


async def generate_schema(source_text: str, user_instruction: str) -> PresentationSchema:
    user_msg = (
        (user_instruction or "Сделай презентацию из этого материала.").strip()
        + "\n\n--- SOURCE ---\n"
        + (source_text or "").strip()
    )[:60000]

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    obj = await _call_litellm(messages)
    obj = _coerce_schema_shape(obj)
    try:
        return PresentationSchema(**obj)
    except ValidationError as e:
        log.warning("schema validation failed (first try): %s ; raw=%s", e, str(obj)[:600])

    retry_messages = messages + [
        {
            "role": "user",
            "content": (
                "Your previous response was not valid JSON matching the schema. "
                'Return a strict JSON object with keys {title, subtitle, cover_image_prompt, '
                'slides:[{title, bullets:["str","str",...], notes, image_prompt}]}. '
                "bullets MUST be a flat list of strings — NOT a dict, NOT objects. "
                "No prose, no markdown."
            ),
        }
    ]
    obj = await _call_litellm(retry_messages)
    obj = _coerce_schema_shape(obj)
    try:
        return PresentationSchema(**obj)
    except ValidationError as e:
        raise SchemaGenerationError(
            f"schema validation failed after retry: {e}; raw={str(obj)[:400]}"
        ) from e


def _coerce_schema_shape(obj: dict) -> dict:
    """Defensive normalisation of common LLM shape drift.

    The schema LLM occasionally returns `bullets` as an object (e.g.
    `{"bullet1": "...", "bullet2": "..."}`) or a list of `{point: str}`
    dicts, or wraps slides in an extra envelope. Normalise before
    pydantic validation so we don't take a 502 every time.
    """
    if not isinstance(obj, dict):
        return obj
    # Some models wrap the result in {"presentation": {...}}
    if "slides" not in obj and isinstance(obj.get("presentation"), dict):
        obj = obj["presentation"]
    slides = obj.get("slides")
    if isinstance(slides, dict):
        # {"slide1": {...}, "slide2": {...}} → list
        slides = list(slides.values())
    if not isinstance(slides, list):
        slides = []
    fixed: list[dict] = []
    for s in slides:
        if not isinstance(s, dict):
            continue
        bullets = s.get("bullets")
        if isinstance(bullets, dict):
            bullets = [str(v) for v in bullets.values() if v is not None]
        elif isinstance(bullets, list):
            flat: list[str] = []
            for b in bullets:
                if isinstance(b, str):
                    flat.append(b)
                elif isinstance(b, dict):
                    # Common shapes: {"text": "..."} / {"point": "..."} / single-value dict
                    if "text" in b:
                        flat.append(str(b["text"]))
                    elif "point" in b:
                        flat.append(str(b["point"]))
                    elif len(b) == 1:
                        flat.append(str(next(iter(b.values()))))
                elif b is not None:
                    flat.append(str(b))
            bullets = flat
        else:
            bullets = []
        s["bullets"] = bullets
        # Coerce optional string-ish fields
        for k in ("notes", "image_prompt", "title"):
            v = s.get(k)
            if isinstance(v, list):
                s[k] = " ".join(str(x) for x in v if x)
            elif v is not None and not isinstance(v, str):
                s[k] = str(v)
        fixed.append(s)
    obj["slides"] = fixed
    for k in ("title", "subtitle", "cover_image_prompt"):
        v = obj.get(k)
        if isinstance(v, list):
            obj[k] = " ".join(str(x) for x in v if x)
        elif v is not None and not isinstance(v, (str, type(None))):
            obj[k] = str(v)
    return obj


async def _call_litellm(messages: list[dict]) -> dict:
    url = f"{LITELLM_URL.rstrip('/')}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": SCHEMA_MODEL,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 4500,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as cli:
            r = await cli.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise SchemaGenerationError(f"LiteLLM transport error: {e}") from e
    if r.status_code != 200:
        raise SchemaGenerationError(f"LiteLLM HTTP {r.status_code}: {r.text[:500]}")
    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"] or "{}"
    except Exception as e:
        raise SchemaGenerationError(f"LiteLLM bad response: {e}") from e
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        raise SchemaGenerationError(f"LiteLLM returned non-JSON: {e}; body={content[:500]}") from e


def _fallback_schema_from_instruction(user_instruction: str) -> Optional[PresentationSchema]:
    """Rarely used — deliberately not called; placeholder for future v2 hook."""
    return None
