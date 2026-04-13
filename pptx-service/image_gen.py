from __future__ import annotations

import asyncio
import base64
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("pptx-service.image_gen")

LITELLM_URL = os.getenv("LITELLM_URL", "http://litellm:4000")
LITELLM_API_KEY = os.getenv("LITELLM_API_KEY", "")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "mws/qwen-image-lightning")
IMAGE_TIMEOUT = int(os.getenv("IMAGE_TIMEOUT", "35"))
IMAGE_SIZE = os.getenv("IMAGE_SIZE", "1024x1024")


async def generate_image(prompt: str) -> Optional[bytes]:
    """Generate a single image via LiteLLM /v1/images/generations.
    Returns bytes on success, None on any failure (caller decides fallback)."""
    if not prompt or not prompt.strip():
        return None
    url = f"{LITELLM_URL.rstrip('/')}/v1/images/generations"
    headers = {"Content-Type": "application/json"}
    if LITELLM_API_KEY:
        headers["Authorization"] = f"Bearer {LITELLM_API_KEY}"
    payload = {
        "model": IMAGE_MODEL,
        "prompt": prompt.strip()[:500],
        "n": 1,
        "size": IMAGE_SIZE,
    }
    try:
        async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as cli:
            r = await cli.post(url, json=payload, headers=headers)
    except Exception as e:
        log.warning("image_gen transport: %s", e)
        return None
    if r.status_code != 200:
        log.warning("image_gen HTTP %s: %s", r.status_code, r.text[:200])
        return None
    try:
        obj = r.json()
        items = obj.get("data") or []
        if not items:
            return None
        first = items[0]
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):
            async with httpx.AsyncClient(timeout=IMAGE_TIMEOUT) as cli:
                d = await cli.get(first["url"])
                if d.status_code == 200:
                    return d.content
                log.warning("image_gen download HTTP %s", d.status_code)
                return None
    except Exception as e:
        log.warning("image_gen parse: %s", e)
        return None
    return None


async def generate_many(prompts: list[Optional[str]]) -> list[Optional[bytes]]:
    """Generate all non-empty prompts in parallel. Preserves positional order,
    puts None where the prompt was empty or generation failed."""
    if not prompts:
        return []

    async def _one(p: Optional[str]) -> Optional[bytes]:
        if not p:
            return None
        return await generate_image(p)

    return await asyncio.gather(*[_one(p) for p in prompts])
