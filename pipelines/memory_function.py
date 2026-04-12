"""
title: MWS Memory
author: MWS GPT
version: 1.0.0
description: Injects relevant user memories into context and extracts new memories from conversations
"""

import json
import urllib.request
from datetime import datetime, timezone
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

    _AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".webm"}
    _DOC_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                 ".txt", ".csv", ".md", ".rtf", ".odt"}

    @classmethod
    def _inject_file_tags(cls, body: dict) -> None:
        """Inject <mws_audio_files> / <mws_doc_files> tags into the last user message.

        OpenWebUI provides body["files"] to inlet filters but strips them
        before calling pipe functions.  By embedding file metadata directly
        into the message text, the auto-router pipe can detect uploads
        and route to the correct subagent (STT for audio, doc_qa for docs).
        """
        # Use files from the CURRENT message (parent_message.files), not
        # body["files"] which accumulates all files across the entire chat.
        metadata = body.get("metadata") or {}
        parent_msg = metadata.get("parent_message") or {}
        files = parent_msg.get("files") or []
        if not files:
            files = body.get("files") or []

        audio_files = []
        doc_files = []
        for f in files:
            inner = f.get("file", {}) if isinstance(f, dict) else {}
            meta = inner.get("meta", {})
            ct = meta.get("content_type", "") or f.get("content_type", "")
            fname = meta.get("name", "") or f.get("name", "")
            fid = inner.get("id", "") or f.get("id", "")
            path = inner.get("path", "")
            fname_lower = fname.lower() if fname else ""

            is_audio = ct.startswith("audio/") or any(
                fname_lower.endswith(ext) for ext in cls._AUDIO_EXTS
            )
            is_doc = ct.startswith("application/pdf") or ct.startswith("text/") or any(
                fname_lower.endswith(ext) for ext in cls._DOC_EXTS
            )

            entry = {"id": fid, "filename": fname, "path": path, "content_type": ct}
            if is_audio:
                audio_files.append(entry)
            elif is_doc:
                doc_files.append(entry)

        tags = ""
        if audio_files:
            tags += "<mws_audio_files>" + json.dumps(audio_files) + "</mws_audio_files>"
        if doc_files:
            tags += "<mws_doc_files>" + json.dumps(doc_files) + "</mws_doc_files>"
        if not tags:
            return

        messages = body.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    msg["content"] = (content + "\n" + tags) if content else tags
                elif isinstance(content, list):
                    content.append({"type": "text", "text": tags})
                break

    def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        """Inject relevant memories and audio file metadata before sending to LLM."""
        # --- Pass audio/document file metadata through to pipe via message tag ---
        # OpenWebUI strips body["files"] before calling pipe functions, but the
        # auto-router needs to know about uploaded audio.  Inject a hidden tag
        # into the last user message so _detect() can pick it up.
        self._inject_file_tags(body)

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

    def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
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
        chat_id = (
            body.get("chat_id")
            or (__metadata__ or {}).get("chat_id")
            or "unknown"
        )

        self._request("POST", "/memories/extract", {
            "user_id": user_id,
            "chat_id": chat_id,
            "messages": recent,
        })

        # Also write a conversation episode (summary + embedding). Errors
        # here must NOT break the chat — swallow and log to debug only.
        try:
            window_size = len(recent)
            total = len(messages)
            clean_msgs = []
            timestamps: list = []
            for m in recent:
                role = m.get("role") or ""
                content = m.get("content")
                if isinstance(content, list):
                    # Strip attachments / non-text parts
                    content = "\n".join(
                        (p.get("text") or "")
                        for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                elif not isinstance(content, str):
                    content = str(content or "")
                clean_msgs.append({"role": role, "content": content})
                ts = m.get("timestamp") or m.get("created_at")
                if ts:
                    timestamps.append(ts)

            if timestamps:
                turn_start_at = min(timestamps)
                turn_end_at = max(timestamps)
                if isinstance(turn_start_at, (int, float)):
                    turn_start_at = datetime.fromtimestamp(
                        turn_start_at, tz=timezone.utc
                    ).isoformat()
                if isinstance(turn_end_at, (int, float)):
                    turn_end_at = datetime.fromtimestamp(
                        turn_end_at, tz=timezone.utc
                    ).isoformat()
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                turn_start_at = now_iso
                turn_end_at = now_iso

            self._request("POST", "/episodes", {
                "user_id": user_id,
                "chat_id": chat_id,
                "messages": clean_msgs,
                "message_indices": [total - window_size, total],
                "turn_start_at": turn_start_at,
                "turn_end_at": turn_end_at,
            })
        except Exception as e:
            print(f"[MWS Memory] episodes write skipped: {e}")

        return body
