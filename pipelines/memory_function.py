"""
title: MWS Memory
author: MWS GPT
version: 1.0.0
description: Injects relevant user memories into context and extracts new memories from conversations
"""

import json
import urllib.request
from typing import Optional

from pydantic import BaseModel, Field


class Filter:
    class Valves(BaseModel):
        memory_service_url: str = Field(
            default="http://memory-service:8000",
            description="Memory Service URL",
        )
        search_limit: int = Field(
            default=5,
            description="Max memories to inject",
        )
        enabled: bool = Field(
            default=True,
            description="Enable memory injection",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        url = f"{self.valves.memory_service_url}{path}"
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            print(f"[MWS Memory] Error: {e}")
            return {}

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Inject relevant memories into the system prompt before sending to LLM."""
        if not self.valves.enabled or not __user__:
            return body

        user_id = __user__.get("id", "")
        messages = body.get("messages", [])
        if not messages:
            return body

        # Get the last user message as search query
        last_msg = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_msg = msg.get("content", "")
                break

        if not last_msg:
            return body

        # Search for relevant memories
        results = self._request("POST", "/memories/search", {
            "user_id": user_id,
            "query": last_msg,
            "limit": self.valves.search_limit,
        })

        if not results or not isinstance(results, list):
            return body

        # Include all results (score filtering disabled until real embeddings)
        memories = results
        if not memories:
            return body

        # Build memory context
        memory_lines = [f"- {m['content']}" for m in memories]
        memory_text = (
            "What you know about this user (from previous conversations):\n"
            + "\n".join(memory_lines)
        )

        # Inject as system message
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] += f"\n\n{memory_text}"
        else:
            messages.insert(0, {"role": "system", "content": memory_text})

        body["messages"] = messages
        return body

    def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Extract memories from the conversation after LLM response."""
        if not self.valves.enabled or not __user__:
            return body

        user_id = __user__.get("id", "")
        messages = body.get("messages", [])

        if len(messages) < 2:
            return body

        # Only extract every 4 messages to avoid excessive API calls
        user_msg_count = sum(1 for m in messages if m.get("role") == "user")
        if user_msg_count % 4 != 0:
            return body

        # Send last messages for extraction
        recent = messages[-8:] if len(messages) > 8 else messages
        chat_id = body.get("chat_id", "unknown")

        self._request("POST", "/memories/extract", {
            "user_id": user_id,
            "chat_id": chat_id,
            "messages": recent,
        })

        return body
