"""
title: MWS Image Generation
author: MWS GPT
version: 0.1.0
description: Pipe that exposes MWS GPT image-generation models as virtual chat models. Picks the latest user message as the prompt and calls LiteLLM /v1/images/generations (the correct modality endpoint) instead of /v1/chat/completions, so "mws/qwen-image" and friends work when selected directly from the model dropdown.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Optional

import httpx
from pydantic import BaseModel, Field


_VIRTUAL_MODELS = [
    {
        "id": "mws-image",
        "name": "MWS Image 🎨",
        "upstream": "mws/qwen-image",
    },
    {
        "id": "mws-image-lightning",
        "name": "MWS Image Lightning ⚡",
        "upstream": "mws/qwen-image-lightning",
    },
]


def _last_user_text(messages: list[dict]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text", "")))
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
    return ""


def _resolve_upstream(model_field: str) -> str:
    # OpenWebUI passes body["model"] as "<function_id>.<pipe_id>", e.g.
    # "mws_image_gen.mws-image-lightning". Take the last segment.
    tail = (model_field or "").split(".")[-1]
    for m in _VIRTUAL_MODELS:
        if m["id"] == tail:
            return m["upstream"]
    return _VIRTUAL_MODELS[0]["upstream"]


class Pipe:
    class Valves(BaseModel):
        litellm_base_url: str = Field(
            default="http://litellm:4000/v1",
            description="LiteLLM proxy base URL (OpenAI-compatible).",
        )
        litellm_api_key: str = Field(
            default=os.getenv("LITELLM_MASTER_KEY", ""),
            description="LiteLLM master key. Falls back to LITELLM_MASTER_KEY env var.",
        )
        size: str = Field(
            default="1024x1024",
            description="Image size passed to /v1/images/generations.",
        )
        request_timeout: int = Field(
            default=180,
            description="HTTP timeout for the upstream call (seconds).",
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [{"id": m["id"], "name": m["name"]} for m in _VIRTUAL_MODELS]

    def _auth_headers(self) -> dict:
        key = self.valves.litellm_api_key or os.getenv("LITELLM_MASTER_KEY", "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
    ) -> AsyncGenerator[str, None]:
        messages = body.get("messages", []) or []
        prompt = _last_user_text(messages)
        upstream = _resolve_upstream(body.get("model", ""))

        if not prompt:
            yield "⚠ Пустой запрос — напишите текстовое описание картинки."
            return

        url = f"{self.valves.litellm_base_url.rstrip('/')}/images/generations"
        payload = {
            "model": upstream,
            "prompt": prompt,
            "n": 1,
            "size": self.valves.size,
        }

        try:
            async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
                r = await cli.post(url, json=payload, headers=self._auth_headers())
                r.raise_for_status()
                obj = r.json()
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.text[:500]
            except Exception:
                pass
            yield f"❌ Ошибка {upstream}: HTTP {e.response.status_code}\n```\n{detail}\n```"
            return
        except Exception as e:
            yield f"❌ Ошибка вызова {upstream}: {type(e).__name__}: {e}"
            return

        items = obj.get("data") or []
        if not items:
            yield f"⚠ {upstream} вернул пустой ответ."
            return

        first = items[0]
        image_url = first.get("url")
        if not image_url and first.get("b64_json"):
            image_url = f"data:image/png;base64,{first['b64_json']}"
        if not image_url:
            yield f"⚠ {upstream}: нет url/b64_json в ответе."
            return

        yield f"🎨 **{upstream}** — `{prompt[:200]}`\n\n![image]({image_url})"
