"""
title: MWS GPT Auto Router
author: MWS GPT
version: 0.9.0
description: Auto-router that detects modality, classifies intent, dispatches subagents in parallel (context-isolated) and streams a final aggregated answer. Virtual model "MWS GPT Auto 🎯".
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import httpx
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class DetectedInput:
    has_image: bool = False
    has_audio: bool = False
    has_document: bool = False
    urls: list[str] = field(default_factory=list)
    lang: str = "en"  # "ru" | "en" | "other"
    last_user_text: str = ""
    image_attachments: list[dict] = field(default_factory=list)
    audio_attachments: list[dict] = field(default_factory=list)
    document_attachments: list[dict] = field(default_factory=list)
    wants_image_gen: bool = False
    wants_web_search: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SubTask:
    kind: str
    input_text: str
    attachments: list[dict] = field(default_factory=list)
    model: str = ""
    max_output_tokens: int = 400
    metadata: dict = field(default_factory=dict)


@dataclass
class CompactResult:
    kind: str
    summary: str = ""
    citations: list[str] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_IMAGE_GEN_RE = re.compile(
    r"(?i)\b(нарисуй|сгенерируй\s+картинк|draw|generate\s+(an?\s+)?image|make\s+an?\s+image)\b"
)
_WEB_SEARCH_RE = re.compile(
    r"(?i)\b(найди\s+в\s+интернете|поищи\s+в\s+сети|поищи\s+в\s+интернете|search\s+the\s+web|look\s+up\s+online|актуальн)\b"
)
_DOC_EXT_RE = re.compile(r"\.(pdf|docx?|txt|md|rtf)$", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")
_REASONER_RE = re.compile(
    r"(?i)(\bдокажи\b|\bдоказать\b|\bдоказательство\b|\bтеорем\w*|\bлемма\w*|"
    r"\bформально\b|\bprove\b|\bproof\b|\btheorem\b|\blemma\b|\bformally\b|"
    r"[∀∃∈∉⊂⊆≡⇒⇔])"
)
_MEMORY_RECALL_RE = re.compile(
    r"(?i)("
    r"о ч[её]м (мы )?(говорили|был разговор|шла речь|шёл разговор|общались|беседовали)|"
    r"что (мы )?обсуждали|когда мы говорили|"
    r"что я тебе (рассказывал|говорил|писал)|"
    r"помнишь,?\s+(как|что|о)|"
    r"(вчера|позавчера|на прошлой неделе|в прошлом месяце|в прошлом году)\b|"
    r"\d+\s+(час|день|дня|дней|недел[юяи]|месяц[ае]?в?|год[а]?|лет)\s+назад|"
    r"what did we (discuss|talk about|say)|do you remember|"
    r"last\s+(week|month|year)|a\s+(week|month|year)\s+ago|"
    r"yesterday\b"
    r")"
)


# ---------------------------------------------------------------------------
# Pipe
# ---------------------------------------------------------------------------


class Pipe:
    class Valves(BaseModel):
        litellm_base_url: str = Field(
            default="http://litellm:4000/v1",
            description="LiteLLM proxy base URL (OpenAI-compatible)",
        )
        litellm_api_key: str = Field(
            default=os.getenv("LITELLM_MASTER_KEY", ""),
            description="LiteLLM master key. Falls back to LITELLM_MASTER_KEY env var.",
        )
        classifier_model: str = Field(
            default="mws/gpt-oss-20b",
            description="Model used by the hybrid classifier (JSON mode).",
        )
        default_ru_model: str = Field(
            default="mws/qwen3-235b",
            description="Default final aggregator model for Russian requests.",
        )
        default_en_model: str = Field(
            default="mws/gpt-alpha",
            description="Default final aggregator model for English requests.",
        )
        max_subagents: int = Field(
            default=4,
            description="Max parallel subagents per request (guard).",
        )
        request_timeout: int = Field(
            default=120,
            description="HTTP timeout for LiteLLM calls (seconds).",
        )
        enabled: bool = Field(default=True, description="Master switch.")
        debug: bool = Field(default=False, description="Verbose routing logs.")

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [{"id": "mws-auto", "name": "MWS GPT Auto 🎯"}]

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__=None,
    ) -> AsyncGenerator[str, None]:
        messages = body.get("messages", []) or []
        files = body.get("files", []) or []
        trace_id = str(uuid.uuid4())
        user_id = (__user__ or {}).get("id")

        detected = self._detect(messages, files)
        if self.valves.debug:
            print(
                f"[mws-auto {trace_id[:8]}] detected={detected.to_dict()} "
                f"user_id={user_id}"
            )

        plan = await self._classify_and_plan(detected, messages, user_id=user_id)
        if self.valves.debug:
            print(
                f"[mws-auto {trace_id[:8]}] plan={[(t.kind, t.model) for t in plan]}"
            )

        yield self._format_routing_block(plan, detected)

        results = await self._dispatch(plan, trace_id=trace_id)

        # Post-process: if stt happened and no chat-subagent yet, re-plan from transcript
        results = await self._maybe_reclassify_stt(
            results, detected, messages, trace_id=trace_id, user_id=user_id
        )

        final_model = (
            self.valves.default_ru_model
            if detected.lang == "ru"
            else self.valves.default_en_model
        )

        async for chunk in self._stream_aggregate(
            final_model, messages, results, detected, trace_id=trace_id
        ):
            yield chunk

        # Append artifacts (generated images) as markdown after stream
        artifact_md = self._render_artifacts(results)
        if artifact_md:
            yield "\n\n" + artifact_md

    # ------------------------------------------------------------------
    # Detector (rules, synchronous)
    # ------------------------------------------------------------------

    def _detect(self, messages: list, files: list) -> DetectedInput:
        det = DetectedInput()
        messages = messages or []
        files = files or []

        # Walk attached files
        for f in files:
            ftype = (f.get("type") or "").lower()
            fname = (f.get("name") or f.get("filename") or "").lower()
            if ftype.startswith("image/") or (
                fname and re.search(r"\.(png|jpe?g|gif|webp|bmp)$", fname)
            ):
                det.has_image = True
                det.image_attachments.append(f)
            elif ftype.startswith("audio/") or (
                fname and re.search(r"\.(mp3|wav|ogg|m4a|flac|webm)$", fname)
            ):
                det.has_audio = True
                det.audio_attachments.append(f)
            elif fname and _DOC_EXT_RE.search(fname):
                det.has_document = True
                det.document_attachments.append(f)

        # Last user message
        last_user_text = ""
        for msg in reversed(messages):
            if (msg or {}).get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                last_user_text = content
            elif isinstance(content, list):
                parts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        parts.append(item.get("text") or "")
                    elif itype == "image_url":
                        det.has_image = True
                        url = (item.get("image_url") or {}).get("url") or ""
                        det.image_attachments.append({"url": url, "type": "image/*"})
                last_user_text = "\n".join(p for p in parts if p)
            break
        det.last_user_text = last_user_text or ""

        # URLs
        det.urls = _URL_RE.findall(det.last_user_text)

        # Language (share of cyrillic)
        if det.last_user_text:
            letters = [c for c in det.last_user_text if c.isalpha()]
            if letters:
                cyr = sum(1 for c in letters if _CYRILLIC_RE.match(c))
                det.lang = "ru" if (cyr / len(letters)) > 0.3 else "en"

        # Intent keywords
        if det.last_user_text:
            det.wants_image_gen = bool(_IMAGE_GEN_RE.search(det.last_user_text))
            det.wants_web_search = bool(_WEB_SEARCH_RE.search(det.last_user_text))

        return det

    # ------------------------------------------------------------------
    # Classifier + planner
    # ------------------------------------------------------------------

    async def _classify_and_plan(
        self,
        detected: DetectedInput,
        messages: list,
        user_id: Optional[str] = None,
    ) -> list[SubTask]:
        plan: list[SubTask] = []

        # Short-circuit by rules
        if detected.has_image and not detected.wants_image_gen:
            vision_model = (
                "mws/cotype-pro-vl"
                if detected.lang == "ru"
                else "mws/qwen3-vl"
            )
            plan.append(
                SubTask(
                    kind="vision",
                    input_text=detected.last_user_text or "Что на изображении?",
                    attachments=detected.image_attachments,
                    model=vision_model,
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )

        if detected.has_audio:
            plan.append(
                SubTask(
                    kind="stt",
                    input_text="",
                    attachments=detected.audio_attachments,
                    model="mws/whisper-turbo",
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )

        if detected.has_document:
            plan.append(
                SubTask(
                    kind="doc_qa",
                    input_text=detected.last_user_text or "Резюмируй документ.",
                    attachments=detected.document_attachments,
                    model="mws/glm-4.6",
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )

        if detected.urls:
            plan.append(
                SubTask(
                    kind="web_fetch",
                    input_text=detected.last_user_text,
                    model="mws/llama-3.1-8b",
                    metadata={
                        "urls": detected.urls,
                        "lang": detected.lang,
                        "user_id": user_id,
                    },
                )
            )

        if detected.wants_image_gen:
            plan.append(
                SubTask(
                    kind="image_gen",
                    input_text=detected.last_user_text,
                    model="mws/qwen-image",
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )

        if detected.wants_web_search:
            plan.append(
                SubTask(
                    kind="web_search",
                    input_text=detected.last_user_text,
                    model="mws/kimi-k2",
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )

        # Short-circuit path taken: if we have at least one signal, return plan as-is.
        # The aggregator itself will produce the final answer — no extra chat subagent.
        if plan:
            return plan[: self.valves.max_subagents]

        # Rule short-circuit: memory_recall. Must run BEFORE long_doc and
        # reasoner so long questions about chat history don't bleed into
        # sa_long_doc / sa_reasoner.
        if _MEMORY_RECALL_RE.search(detected.last_user_text or ""):
            plan.append(
                SubTask(
                    kind="memory_recall",
                    input_text=detected.last_user_text,
                    metadata={"user_id": user_id, "lang": detected.lang},
                )
            )
            return plan[: self.valves.max_subagents]

        # Rule short-circuit: long user input → sa_long_doc / mws/glm-4.6.
        # Anything ≥1500 chars is almost certainly a document/transcript, not a
        # chat turn, and glm-4.6 handles long context better than qwen3-235b.
        # This runs before the reasoner/LLM-classifier branches so long math
        # proofs still go to reasoner (short) but long narrative text goes here.
        if len(detected.last_user_text or "") >= 1500 and not _REASONER_RE.search(
            detected.last_user_text or ""
        ):
            plan.append(
                SubTask(
                    kind="long_doc",
                    input_text=detected.last_user_text,
                    model="mws/glm-4.6",
                    metadata={
                        "lang": detected.lang,
                        "rule": "long_text_regex",
                        "user_id": user_id,
                    },
                )
            )
            return plan[: self.valves.max_subagents]

        # Rule short-circuit: formal proofs / math reasoning → sa_reasoner.
        # The LLM classifier tends to label "Докажи, что…" as generic chat,
        # but we want deepseek-r1-32b to handle the CoT and strip it.
        if _REASONER_RE.search(detected.last_user_text or ""):
            plan.append(
                SubTask(
                    kind="reasoner",
                    input_text=detected.last_user_text,
                    model="mws/deepseek-r1-32b",
                    metadata={
                        "lang": detected.lang,
                        "rule": "reasoner_regex",
                        "user_id": user_id,
                    },
                )
            )
            return plan[: self.valves.max_subagents]

        # Pure text — call LLM classifier
        kind, model, time_window = await self._llm_classify(detected)
        meta: dict = {"lang": detected.lang, "user_id": user_id}
        if time_window:
            meta["time_window"] = time_window
        plan.append(
            SubTask(
                kind=kind,
                input_text=detected.last_user_text,
                model=model,
                metadata=meta,
            )
        )
        return plan[: self.valves.max_subagents]

    async def _llm_classify(
        self, detected: DetectedInput
    ) -> tuple[str, str, Optional[dict]]:
        """Return (kind, model, time_window) for pure-text requests. Falls back safely on error."""
        fallback_kind = "ru_chat" if detected.lang == "ru" else "general"
        fallback_model = (
            self.valves.default_ru_model
            if detected.lang == "ru"
            else self.valves.default_en_model
        )

        if not detected.last_user_text.strip():
            return fallback_kind, fallback_model, None

        today = datetime.now(timezone.utc).date().isoformat()
        system = (
            f"Current date: {today}\n"
            "You are a router. Classify the user request and return a JSON object "
            'with fields: {"intents": [...], "lang": "ru"|"en"|"other", '
            '"complexity": "trivial"|"normal"|"hard", "primary_model": "mws/...", '
            '"reason": "<one sentence>", "time_window": {"from":"<ISO>","to":"<ISO>"}}. '
            "Valid intents: code, math, ru_chat, general, long_doc, deep_research, "
            "presentation, memory_recall. "
            "Valid primary_model: mws/gpt-alpha, mws/qwen3-235b, mws/qwen3-coder, "
            "mws/deepseek-r1-32b, mws/glm-4.6, mws/kimi-k2, mws/llama-3.1-8b. "
            "\n\nCRITICAL RULE — memory_recall:\n"
            "If the user asks ANYTHING about past conversations, prior dialogs, "
            "previously discussed topics, what they told you before, or what "
            "happened in an earlier session — intent MUST be memory_recall. "
            "This includes ALL semantic variations, not just specific keywords. "
            "Examples of memory_recall (do NOT classify these as ru_chat/general): "
            "'о чём мы говорили', 'о чём был разговор', 'какая была тема', "
            "'что мы обсуждали', 'помнишь, что я рассказывал', 'вспомни наш диалог', "
            "'про что шла речь', 'о чём я тебя спрашивал', 'какая была тема нашего "
            "позавчерашнего разговора', 'что было вчера в чате', 'what did we discuss', "
            "'do you remember our chat', 'what was our last topic'. "
            "If the user also mentions a time marker (вчера, позавчера, 3 months ago, "
            "last week, неделю назад, месяц назад), ALSO return "
            'time_window: {"from":"<ISO-8601>","to":"<ISO-8601>"} relative to the '
            "current date above (use a sensible window: for 'yesterday'/'вчера' — full "
            "previous day; for 'last week' — previous 7 days; for 'N months ago' — "
            "a ±15-day window around that date). Otherwise omit time_window.\n\n"
            "Other intents examples: "
            '"write fibonacci in rust" -> {"intents":["code"],"primary_model":"mws/qwen3-coder",...}; '
            '"Провести глубокое исследование рынка EV" -> {"intents":["deep_research"],"primary_model":"mws/kimi-k2",...}; '
            '"Сделай презентацию про Python" -> {"intents":["presentation"],"primary_model":"mws/gpt-alpha",...}; '
            '"Привет, как дела?" -> {"intents":["ru_chat"],"primary_model":"mws/qwen3-235b",...}; '
            '"о чём мы говорили неделю назад" -> {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-04-01T00:00:00Z","to":"2026-04-08T23:59:59Z"}}; '
            '"какая была тема позавчерашнего разговора" -> {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-04-10T00:00:00Z","to":"2026-04-10T23:59:59Z"}}; '
            '"о чём был разговор вчера" -> {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-04-11T00:00:00Z","to":"2026-04-11T23:59:59Z"}}.'
        )
        try:
            resp = await self._call_litellm(
                model=self.valves.classifier_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": detected.last_user_text[:2000]},
                ],
                temperature=0,
                max_tokens=250,
                response_format={"type": "json_object"},
            )
            content = (
                resp.get("choices", [{}])[0].get("message", {}).get("content") or "{}"
            )
            data = json.loads(content)
        except Exception as e:
            if self.valves.debug:
                print(f"[mws-auto] classifier error: {e}")
            return fallback_kind, fallback_model, None

        intents = data.get("intents") or []
        primary_model = data.get("primary_model") or fallback_model
        intent = intents[0] if intents else fallback_kind
        # Map intent → subagent kind
        kind_map = {
            "code": "code",
            "math": "reasoner",
            "ru_chat": "ru_chat",
            "general": "general",
            "long_doc": "long_doc",
            "deep_research": "deep_research",
            "presentation": "presentation",
            "memory_recall": "memory_recall",
        }
        kind = kind_map.get(intent, fallback_kind)
        # Lang-aware override: gpt-oss-20b frequently returns intent="general"
        # even for Russian text, which routes to sa_general / mws/gpt-alpha.
        # For RU we prefer sa_ru_chat / mws/qwen3-235b so the Routing decision
        # block matches the detected language.
        if detected.lang == "ru" and kind == "general":
            kind = "ru_chat"
            if primary_model in ("mws/gpt-alpha", fallback_model):
                primary_model = self.valves.default_ru_model
        tw = data.get("time_window")
        if isinstance(tw, dict) and (tw.get("from") or tw.get("to")):
            time_window: Optional[dict] = {
                "from": tw.get("from"),
                "to": tw.get("to"),
            }
        else:
            time_window = None
        return kind, primary_model, time_window

    # ------------------------------------------------------------------
    # Routing-decision block
    # ------------------------------------------------------------------

    def _format_routing_block(
        self, plan: list[SubTask], detected: DetectedInput
    ) -> str:
        subagents = [t.kind for t in plan]
        models = [t.model or "-" for t in plan]
        return (
            "<details>\n<summary>🎯 Routing decision</summary>\n\n"
            f"- **Lang:** `{detected.lang}`\n"
            f"- **Subagents:** `{subagents}`\n"
            f"- **Models:** `{models}`\n"
            f"- **Signals:** image={detected.has_image}, audio={detected.has_audio}, "
            f"doc={detected.has_document}, urls={len(detected.urls)}, "
            f"img_gen={detected.wants_image_gen}, web_search={detected.wants_web_search}\n"
            "\n</details>\n\n"
        )

    # ------------------------------------------------------------------
    # LiteLLM HTTP helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict:
        key = self.valves.litellm_api_key or os.getenv("LITELLM_MASTER_KEY", "")
        h = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    async def _call_litellm(
        self, model: str, messages: list, **kwargs
    ) -> dict:
        """POST /chat/completions (non-streaming)."""
        payload = {"model": model, "messages": messages, "stream": False, **kwargs}
        url = f"{self.valves.litellm_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
            r = await cli.post(url, json=payload, headers=self._auth_headers())
            r.raise_for_status()
            return r.json()

    async def _call_litellm_stream(
        self, model: str, messages: list, **kwargs
    ) -> AsyncGenerator[str, None]:
        """POST /chat/completions with stream=True, yield content chunks."""
        payload = {"model": model, "messages": messages, "stream": True, **kwargs}
        url = f"{self.valves.litellm_base_url.rstrip('/')}/chat/completions"
        async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
            async with cli.stream(
                "POST", url, json=payload, headers=self._auth_headers()
            ) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = (
                            obj.get("choices", [{}])[0]
                            .get("delta", {})
                            .get("content")
                        )
                        if delta:
                            yield delta
                    except Exception:
                        continue

    @staticmethod
    def _truncate_tokens(text: str, approx_tokens: int = 500) -> str:
        """Cheap character-based truncation (~4 chars per token)."""
        if not text:
            return ""
        limit = approx_tokens * 4
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + " …[truncated]"

    # ------------------------------------------------------------------
    # Dispatcher
    # ------------------------------------------------------------------

    async def _run_subagent(self, task: SubTask, trace_id: str) -> CompactResult:
        dispatch = {
            "general": self._sa_general,
            "ru_chat": self._sa_ru_chat,
            "code": self._sa_code,
            "reasoner": self._sa_reasoner,
            "long_doc": self._sa_long_doc,
            "vision": self._sa_vision,
            "stt": self._sa_stt,
            "image_gen": self._sa_image_gen,
            "web_fetch": self._sa_web_fetch,
            "web_search": self._sa_web_search,
            "doc_qa": self._sa_doc_qa,
            "deep_research": self._sa_deep_research,
            "presentation": self._sa_presentation,
            "memory_recall": self._sa_memory_recall,
        }
        handler = dispatch.get(task.kind)
        if handler is None:
            return CompactResult(
                kind=task.kind, error=f"unknown subagent kind: {task.kind}"
            )
        try:
            result = await handler(task)
            if self.valves.debug:
                print(
                    f"[mws-auto {trace_id[:8]}] sa_{task.kind} → "
                    f"{len(result.summary)} chars, error={result.error}"
                )
            return result
        except Exception as e:
            if self.valves.debug:
                print(f"[mws-auto {trace_id[:8]}] sa_{task.kind} failed: {e}")
            return CompactResult(kind=task.kind, error=str(e))

    async def _dispatch(
        self, plan: list[SubTask], trace_id: str
    ) -> list[CompactResult]:
        if not plan:
            return []
        coros = [self._run_subagent(t, trace_id=trace_id) for t in plan]
        return await asyncio.gather(*coros, return_exceptions=False)

    async def _maybe_reclassify_stt(
        self,
        results: list[CompactResult],
        detected: DetectedInput,
        messages: list,
        trace_id: str,
        user_id: Optional[str] = None,
    ) -> list[CompactResult]:
        """If sa_stt ran and there is no chat/text subagent yet, re-plan from transcript."""
        stt_result = next(
            (r for r in results if r.kind == "stt" and not r.error and r.summary),
            None,
        )
        if stt_result is None:
            return results
        has_chat = any(
            r.kind in {"general", "ru_chat", "code", "reasoner", "long_doc"}
            for r in results
        )
        if has_chat:
            return results
        transcript = stt_result.summary
        # Build synthetic DetectedInput from transcript
        synth = DetectedInput(last_user_text=transcript, lang=detected.lang)
        letters = [c for c in transcript if c.isalpha()]
        if letters:
            cyr = sum(1 for c in letters if _CYRILLIC_RE.match(c))
            synth.lang = "ru" if (cyr / len(letters)) > 0.3 else "en"
        synth.urls = _URL_RE.findall(transcript)
        synth.wants_image_gen = bool(_IMAGE_GEN_RE.search(transcript))
        synth.wants_web_search = bool(_WEB_SEARCH_RE.search(transcript))
        new_plan = await self._classify_and_plan(
            synth, messages, user_id=user_id
        )
        # Skip duplicates already handled
        existing_kinds = {r.kind for r in results}
        new_plan = [t for t in new_plan if t.kind not in existing_kinds]
        if not new_plan:
            return results
        extra = await self._dispatch(new_plan, trace_id=trace_id)
        return results + extra

    # ------------------------------------------------------------------
    # Aggregator (streaming)
    # ------------------------------------------------------------------

    async def _stream_aggregate(
        self,
        final_model: str,
        messages: list,
        results: list[CompactResult],
        detected: DetectedInput,
        trace_id: str,
    ) -> AsyncGenerator[str, None]:
        # Build scratchpad from compact summaries only
        lines: list[str] = []
        for r in results:
            if r.error:
                lines.append(f"[sa_{r.kind}] (ошибка: {r.error})")
                continue
            if not r.summary:
                continue
            block = f"[sa_{r.kind}] {r.summary}"
            if r.citations:
                block += "\nCitations: " + ", ".join(r.citations)
            lines.append(block)
        scratchpad = "\n\n".join(lines) if lines else "(нет результатов субагентов)"

        lang_instr = (
            "Отвечай на русском языке в markdown."
            if detected.lang == "ru"
            else "Answer in English using markdown."
        )
        system_prompt = (
            'Ты — финальный агент "MWS GPT Auto". Ниже — результаты работы вспомогательных '
            "субагентов. Используй их как факты. Не показывай пользователю внутреннюю "
            "кухню и не дублируй служебные теги вроде [sa_*]. "
            "Если среди результатов есть ошибки — кратко упомяни, но продолжи отвечать. "
            "Если есть artifacts (изображения) — они будут добавлены в сообщение после "
            "твоего ответа, можешь их анонсировать одной строкой. "
            f"{lang_instr}\n\n--- SUBAGENT RESULTS ---\n{scratchpad}"
        )

        # Keep the user's most recent message as the final user turn
        user_msg = self._last_user_message(messages)
        final_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]

        try:
            async for chunk in self._call_litellm_stream(
                final_model,
                final_messages,
                temperature=0.7,
                max_tokens=1200,
            ):
                yield chunk
        except Exception as e:
            # Non-streaming fallback
            try:
                resp = await self._call_litellm(
                    final_model,
                    final_messages,
                    temperature=0.7,
                    max_tokens=1200,
                )
                yield (
                    resp.get("choices", [{}])[0].get("message", {}).get("content")
                    or f"(агрегатор недоступен: {e})"
                )
            except Exception as e2:
                yield f"\n\n⚠️ Финальная модель недоступна: {e2}"

    def _last_user_message(self, messages: list) -> str:
        for msg in reversed(messages or []):
            if (msg or {}).get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    i.get("text") or ""
                    for i in content
                    if isinstance(i, dict) and i.get("type") == "text"
                )
        return ""

    def _render_artifacts(self, results: list[CompactResult]) -> str:
        out: list[str] = []
        for r in results:
            for art in r.artifacts or []:
                if art.get("type") == "image" and art.get("url"):
                    out.append(f"![generated]({art['url']})")
        return "\n".join(out)

    # ------------------------------------------------------------------
    # Text subagents
    # ------------------------------------------------------------------

    async def _text_subagent(
        self, model: str, system: str, task: SubTask, temperature: float = 0.7
    ) -> CompactResult:
        resp = await self._call_litellm(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": task.input_text[:8000]},
            ],
            temperature=temperature,
            max_tokens=task.max_output_tokens or 500,
        )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        return CompactResult(kind=task.kind, summary=self._truncate_tokens(text, 500))

    async def _sa_general(self, task: SubTask) -> CompactResult:
        return await self._text_subagent(
            model=task.model or "mws/gpt-alpha",
            system="You are a helpful, concise assistant. Answer in English in markdown.",
            task=task,
            temperature=0.7,
        )

    async def _sa_ru_chat(self, task: SubTask) -> CompactResult:
        return await self._text_subagent(
            model=task.model or "mws/qwen3-235b",
            system="Ты — дружелюбный и лаконичный ассистент. Отвечай на русском в markdown.",
            task=task,
            temperature=0.7,
        )

    async def _sa_code(self, task: SubTask) -> CompactResult:
        return await self._text_subagent(
            model=task.model or "mws/qwen3-coder",
            system=(
                "You are an expert software engineer. Produce clean, idiomatic code "
                "with brief explanations. Use markdown code blocks with language tags."
            ),
            task=task,
            temperature=0.3,
        )

    async def _sa_reasoner(self, task: SubTask) -> CompactResult:
        resp = await self._call_litellm(
            model=task.model or "mws/deepseek-r1-32b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a careful reasoner. Think step-by-step, then give a "
                        "concise final answer after the line `### Answer:`."
                    ),
                },
                {"role": "user", "content": task.input_text[:8000]},
            ],
            temperature=0.3,
            max_tokens=task.max_output_tokens or 800,
        )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        )
        # Keep only the part after "### Answer:" — reasoning is thrown away for isolation
        marker = "### Answer:"
        if marker in text:
            text = text.split(marker, 1)[1].strip()
        else:
            text = text.strip()
        return CompactResult(
            kind="reasoner", summary=self._truncate_tokens(text, 500)
        )

    async def _sa_long_doc(self, task: SubTask) -> CompactResult:
        return await self._text_subagent(
            model=task.model or "mws/glm-4.6",
            system=(
                "You analyze long documents. Be precise, cite sections when possible. "
                "Answer in markdown."
            ),
            task=task,
            temperature=0.5,
        )

    # ------------------------------------------------------------------
    # Multimodal subagents
    # ------------------------------------------------------------------

    _VISION_BLIND_RE = re.compile(
        r"(?i)(i\s+(don't|do not|cannot|can't)\s+see\s+(an?\s+)?image|"
        r"no\s+image\s+(was\s+)?(provided|attached|shared)|"
        r"не\s+вижу\s+(изображени|картинк)|"
        r"изображение\s+не\s+(предоставлено|прикреплено))"
    )

    async def _sa_vision(self, task: SubTask) -> CompactResult:
        content: list[dict] = [
            {"type": "text", "text": task.input_text or "Опиши изображение."}
        ]
        for att in task.attachments or []:
            url = att.get("url") or att.get("image_url") or ""
            if not url and att.get("data"):
                b64 = att["data"]
                mime = att.get("type") or "image/png"
                url = f"data:{mime};base64,{b64}"
            if url:
                content.append({"type": "image_url", "image_url": {"url": url}})
        if len(content) == 1:
            return CompactResult(
                kind="vision", error="no image attachment available"
            )

        primary = task.model or "mws/cotype-pro-vl"
        # cotype-pro-vl is proven working in smoke tests; use it as fallback
        # whenever the primary silently drops the image.
        fallback = (
            "mws/cotype-pro-vl" if primary != "mws/cotype-pro-vl" else "mws/qwen3-vl"
        )

        async def _call(model: str) -> str:
            resp = await self._call_litellm(
                model=model,
                messages=[{"role": "user", "content": content}],
                temperature=0.3,
                max_tokens=task.max_output_tokens or 500,
            )
            return (
                resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
            ).strip()

        text = await _call(primary)
        if not text or self._VISION_BLIND_RE.search(text):
            try:
                retry_text = await _call(fallback)
                if retry_text and not self._VISION_BLIND_RE.search(retry_text):
                    return CompactResult(
                        kind="vision",
                        summary=self._truncate_tokens(retry_text, 500),
                        metadata={"primary": primary, "used": fallback},
                    )
            except Exception:
                pass

        return CompactResult(kind="vision", summary=self._truncate_tokens(text, 500))

    async def _sa_stt(self, task: SubTask) -> CompactResult:
        """Transcribe audio via LiteLLM /audio/transcriptions (multipart)."""
        att = (task.attachments or [None])[0]
        if not att:
            return CompactResult(kind="stt", error="no audio attachment")

        audio_bytes: Optional[bytes] = None
        filename = att.get("name") or att.get("filename") or "audio.mp3"
        if att.get("data"):
            try:
                audio_bytes = base64.b64decode(att["data"])
            except Exception as e:
                return CompactResult(kind="stt", error=f"bad base64: {e}")
        elif att.get("url"):
            url = att["url"]
            async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
                r = await cli.get(url)
                r.raise_for_status()
                audio_bytes = r.content

        if not audio_bytes:
            return CompactResult(kind="stt", error="unable to load audio bytes")

        url = f"{self.valves.litellm_base_url.rstrip('/')}/audio/transcriptions"
        data = {"model": task.model or "mws/whisper-turbo"}
        if task.metadata.get("lang") == "ru":
            data["language"] = "ru"
        files_part = {"file": (filename, audio_bytes, "application/octet-stream")}
        headers = {"Authorization": self._auth_headers().get("Authorization", "")}
        async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
            r = await cli.post(url, data=data, files=files_part, headers=headers)
            r.raise_for_status()
            obj = r.json()
        transcript = (obj.get("text") or "").strip()
        return CompactResult(
            kind="stt",
            summary=self._truncate_tokens(transcript, 500),
            metadata={"full_transcript": transcript},
        )

    async def _sa_image_gen(self, task: SubTask) -> CompactResult:
        url = f"{self.valves.litellm_base_url.rstrip('/')}/images/generations"
        payload = {
            "model": task.model or "mws/qwen-image",
            "prompt": task.input_text,
            "n": 1,
            "size": "1024x1024",
        }
        async with httpx.AsyncClient(timeout=self.valves.request_timeout) as cli:
            r = await cli.post(url, json=payload, headers=self._auth_headers())
            r.raise_for_status()
            obj = r.json()
        items = obj.get("data") or []
        if not items:
            return CompactResult(kind="image_gen", error="empty image response")
        first = items[0]
        image_url = first.get("url")
        if not image_url and first.get("b64_json"):
            image_url = f"data:image/png;base64,{first['b64_json']}"
        if not image_url:
            return CompactResult(kind="image_gen", error="no url/b64 in response")
        return CompactResult(
            kind="image_gen",
            summary=f"Сгенерировано изображение по запросу: {task.input_text[:200]}",
            artifacts=[{"type": "image", "url": image_url}],
        )

    # ------------------------------------------------------------------
    # Web subagents
    # ------------------------------------------------------------------

    async def _fetch_url_text(self, url: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 MWS-GPT-Hub"}
        async with httpx.AsyncClient(
            timeout=10, headers=headers, follow_redirects=True
        ) as cli:
            r = await cli.get(url)
            r.raise_for_status()
            html = r.text
        # Strip scripts/styles, then tags
        html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
        html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
        html = re.sub(r"<nav[\s\S]*?</nav>", " ", html, flags=re.IGNORECASE)
        html = re.sub(r"<footer[\s\S]*?</footer>", " ", html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:6000]

    async def _sa_web_fetch(self, task: SubTask) -> CompactResult:
        urls = task.metadata.get("urls") or _URL_RE.findall(task.input_text or "")
        if not urls:
            return CompactResult(kind="web_fetch", error="no URL found")
        url = urls[0]
        try:
            page_text = await self._fetch_url_text(url)
        except Exception as e:
            return CompactResult(kind="web_fetch", error=f"fetch failed: {e}")
        if not page_text:
            return CompactResult(kind="web_fetch", error="empty page body")
        lang_hint = (
            "Отвечай на русском." if task.metadata.get("lang") == "ru" else "Answer in English."
        )
        resp = await self._call_litellm(
            model=task.model or "mws/llama-3.1-8b",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — суммаризатор веб-страниц. Выдели главное в 3-5 предложениях. "
                        + lang_hint
                    ),
                },
                {"role": "user", "content": page_text},
            ],
            temperature=0.3,
            max_tokens=400,
        )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        summary = f"{text}\n\nИсточник: {url}"
        return CompactResult(
            kind="web_fetch",
            summary=self._truncate_tokens(summary, 500),
            citations=[url],
        )

    async def _ddg_search(self, query: str, n: int = 3) -> list[dict]:
        """Lightweight DuckDuckGo HTML search. Returns [{title,url,snippet}]."""
        url = "https://duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 MWS-GPT-Hub"}
        async with httpx.AsyncClient(
            timeout=10, headers=headers, follow_redirects=True
        ) as cli:
            r = await cli.post(url, data={"q": query})
            r.raise_for_status()
            html = r.text
        # Very small HTML parser — targets DuckDuckGo HTML results layout
        results: list[dict] = []
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
            r'[\s\S]*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html,
        ):
            href, title, snippet = m.group(1), m.group(2), m.group(3)
            title = re.sub(r"<[^>]+>", "", title).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            if href.startswith("//duckduckgo.com/l/?uddg="):
                import urllib.parse as _u

                parsed = _u.parse_qs(href.split("?", 1)[1])
                real = parsed.get("uddg", [href])[0]
                href = _u.unquote(real)
            results.append({"title": title, "url": href, "snippet": snippet})
            if len(results) >= n:
                break
        return results

    async def _sa_web_search(self, task: SubTask) -> CompactResult:
        query = task.input_text
        if not query:
            return CompactResult(kind="web_search", error="empty query")
        try:
            hits = await self._ddg_search(query, n=3)
        except Exception as e:
            return CompactResult(kind="web_search", error=f"ddg failed: {e}")
        if not hits:
            return CompactResult(kind="web_search", error="no search results")

        async def _safe_fetch(u: str) -> str:
            try:
                return (await self._fetch_url_text(u))[:2000]
            except Exception:
                return ""

        bodies = await asyncio.gather(*[_safe_fetch(h["url"]) for h in hits])
        snippets_block = "\n\n".join(
            f"[{i+1}] {h['title']} — {h['url']}\n{body or h['snippet']}"
            for i, (h, body) in enumerate(zip(hits, bodies))
        )
        lang_hint = (
            "Отвечай на русском." if task.metadata.get("lang") == "ru" else "Answer in English."
        )
        resp = await self._call_litellm(
            model=task.model or "mws/kimi-k2",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Ты — поисковый агент. Ответь на вопрос пользователя, опираясь "
                        "на найденные фрагменты. Цитируй источники как [1], [2], [3]. "
                        + lang_hint
                    ),
                },
                {
                    "role": "user",
                    "content": f"Вопрос: {query}\n\nФрагменты:\n{snippets_block}",
                },
            ],
            temperature=0.4,
            max_tokens=500,
        )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        return CompactResult(
            kind="web_search",
            summary=self._truncate_tokens(text, 500),
            citations=[h["url"] for h in hits],
        )

    # ------------------------------------------------------------------
    # Doc Q&A
    # ------------------------------------------------------------------

    async def _sa_doc_qa(self, task: SubTask) -> CompactResult:
        """
        Variant A — rely on OpenWebUI's built-in RAG pipeline.

        OpenWebUI, when `RAG_EMBEDDING_MODEL=mws/bge-m3` is configured (see
        docker-compose.yml), automatically processes uploaded files and injects
        retrieved chunks into the assistant's system/user context before calling
        the selected model. For a Pipe function, those chunks surface inside the
        `body["messages"]` we already receive — i.e. the relevant context is
        already in `task.input_text` (the classifier packs last-user-text there)
        or inside `task.attachments` metadata. We do NOT call OpenWebUI's
        retrieval API ourselves, to avoid double-indexing BGE-M3.

        Fallback: if `task.attachments` exposes raw text (rare), we pack it here;
        otherwise we ask glm-4.6 to answer based on whatever context is present.
        """
        context_parts: list[str] = []
        for att in task.attachments or []:
            txt = att.get("text") or att.get("content") or ""
            if isinstance(txt, str) and txt.strip():
                context_parts.append(f"--- {att.get('name','document')} ---\n{txt}")
        context = "\n\n".join(context_parts)
        # Cap context to stay well under glm-4.6's 200K window
        if len(context) > 100_000:
            context = context[:100_000] + "\n…[truncated, rely on RAG]"
        user_msg = task.input_text or "Резюмируй документ."
        if context:
            user_msg = f"{user_msg}\n\n--- DOCUMENT ---\n{context}"

        resp = await self._call_litellm(
            model=task.model or "mws/glm-4.6",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Отвечай на вопрос по предоставленному документу. "
                        "Цитируй разделы/страницы, если указаны. "
                        "Если ответа в документе нет — скажи прямо."
                    ),
                },
                {"role": "user", "content": user_msg[:150_000]},
            ],
            temperature=0.3,
            max_tokens=task.max_output_tokens or 700,
        )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        citations = [
            att.get("name") or att.get("filename") or "document"
            for att in task.attachments or []
        ]
        return CompactResult(
            kind="doc_qa",
            summary=self._truncate_tokens(text, 500),
            citations=citations,
        )

    # ------------------------------------------------------------------
    # Memory recall
    # ------------------------------------------------------------------

    async def _sa_memory_recall(self, task: SubTask) -> CompactResult:
        user_id = task.metadata.get("user_id")
        if not user_id:
            return CompactResult(
                kind="memory_recall",
                summary="",
                error="memory_recall: no user_id in metadata",
            )
        payload: dict = {
            "user_id": user_id,
            "query": task.input_text,
            "limit": 5,
        }
        tw = task.metadata.get("time_window") or {}
        if tw.get("from"):
            payload["date_from"] = tw["from"]
        if tw.get("to"):
            payload["date_to"] = tw["to"]
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.post(
                    "http://memory-service:8000/episodes/recall",
                    json=payload,
                )
                r.raise_for_status()
                episodes = r.json()
        except Exception as e:
            return CompactResult(
                kind="memory_recall",
                summary="",
                error=f"memory_recall request failed: {e}",
            )
        if not episodes:
            return CompactResult(
                kind="memory_recall",
                summary="В истории диалогов ничего не найдено по этому запросу.",
            )
        lines: list[str] = []
        for ep in episodes:
            date = (ep.get("turn_end_at") or "")[:10]
            lines.append(f"- [{date}] {(ep.get('summary') or '').strip()}")
        body = "Найденные эпизоды из прошлых диалогов:\n" + "\n".join(lines)
        return CompactResult(
            kind="memory_recall",
            summary=self._truncate_tokens(body, 500),
            citations=[ep.get("chat_id") for ep in episodes if ep.get("chat_id")],
        )

    # ------------------------------------------------------------------
    # Stubs (v2)
    # ------------------------------------------------------------------

    async def _sa_deep_research(self, task: SubTask) -> CompactResult:
        return CompactResult(
            kind="deep_research",
            summary=(
                "⚠️ **Deep Research** будет добавлен в v2.\n\n"
                "В следующей версии я смогу провести многошаговое исследование: "
                "собрать факты из нескольких источников, проверить их и сделать вывод. "
                "Пока могу ответить на основе одного поискового запроса — попросите "
                '"поищи в интернете ...".'
            ),
        )

    async def _sa_presentation(self, task: SubTask) -> CompactResult:
        return CompactResult(
            kind="presentation",
            summary=(
                "⚠️ **Генерация презентаций** будет добавлена в v2.\n\n"
                "В v2 я смогу сгенерировать Marp/Reveal.js markdown с картинками. "
                "Пока могу составить структуру презентации в markdown — просто "
                "попросите план слайдов."
            ),
        )
