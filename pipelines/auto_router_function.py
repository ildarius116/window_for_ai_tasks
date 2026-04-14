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
import pathlib
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
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
    r"(?iu)("
    r"нарису\w*|нарисова\w*|рису\w*|"
    r"изобрази\w*|"
    r"(сгенерируй|сгенерировать|создай|сделай|построй)\s+(мне\s+)?(картинк\w*|изображени\w*|иллюстраци\w*|рисун\w*|арт|фото)|"
    r"\bdraw\b|\bpaint\b|generate\s+(an?\s+)?(image|picture|illustration)|make\s+(an?\s+)?(image|picture|illustration)|create\s+(an?\s+)?(image|picture|illustration)"
    r")"
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

# Phase 11 — presentation safety-net markers (applied AFTER _llm_classify, not as primary gate).
_PPTX_MARKERS = (
    "презентация", "презентацию", "презентацией", "презентации", "презентаций",
    "слайды", "слайдов", "слайд ", "слайда", "слайдам", "слайдах",
    "pptx", "powerpoint", "power point", "keynote", "реферат в слайдах",
    "представление",
    "presentation", "slides", "slide deck", "deck",
)


def _looks_like_presentation(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _PPTX_MARKERS)


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

        has_artifacts = any(
            art.get("url") for r in results for art in (r.artifacts or [])
        )
        if has_artifacts:
            # Buffer the full response so we can strip hallucinated image/file
            # markers before the real artifacts are appended by _render_artifacts.
            buf: list[str] = []
            async for chunk in self._stream_aggregate(
                final_model, messages, results, detected, trace_id=trace_id
            ):
                buf.append(chunk)
            text = "".join(buf)
            text = self._scrub_artifact_echoes(text)
            yield text
        else:
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

        # Last user message.
        # NOTE: OpenWebUI can inject prior-turn citations/sources as extra text
        # parts inside the current user message's `content` list. Joining all
        # parts leaks old URLs and text into the current turn and skews routing
        # (observed: follow-up "weather in Moscow" still carried a google.com
        # URL from an earlier turn and got force-routed to web_fetch). Take
        # only the LAST non-empty text part — conventionally the user's actual
        # typed input — and ignore injected context parts.
        last_user_text = ""
        for msg in reversed(messages):
            if (msg or {}).get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                last_user_text = content
            elif isinstance(content, list):
                last_text_part = ""
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        t = item.get("text") or ""
                        if t.strip():
                            last_text_part = t
                    elif itype == "image_url":
                        det.has_image = True
                        url = (item.get("image_url") or {}).get("url") or ""
                        det.image_attachments.append({"url": url, "type": "image/*"})
                last_user_text = last_text_part
            break
        # Parse <mws_audio_files> tag injected by inlet filter
        _audio_tag_re = re.compile(
            r"<mws_audio_files>(.*?)</mws_audio_files>", re.DOTALL
        )
        _audio_match = _audio_tag_re.search(last_user_text)
        if _audio_match:
            try:
                _audio_list = json.loads(_audio_match.group(1))
                for af in _audio_list:
                    det.has_audio = True
                    det.audio_attachments.append(af)
            except Exception:
                pass
            # Strip the tag from user text so it doesn't confuse the LLM
            last_user_text = _audio_tag_re.sub("", last_user_text).strip()

        # Parse <mws_doc_files> tag injected by inlet filter
        _doc_tag_re = re.compile(
            r"<mws_doc_files>(.*?)</mws_doc_files>", re.DOTALL
        )
        _doc_match = _doc_tag_re.search(last_user_text)
        if _doc_match:
            try:
                _doc_list = json.loads(_doc_match.group(1))
                for df in _doc_list:
                    det.has_document = True
                    det.document_attachments.append(df)
            except Exception:
                pass
            last_user_text = _doc_tag_re.sub("", last_user_text).strip()

        # --- Strip OpenWebUI RAG template wrapper ---
        # When OWUI has retrieval/web_search enabled and the chat has a
        # knowledge source (e.g. a URL previously fetched by sa_web_fetch),
        # it wraps every follow-up user turn in a template like:
        #
        #   ### Task: ...instructions...
        #   <context>
        #   <source id="1" name="https://..."> ...full page content... </source>
        #   </context>
        #
        #   *   <user's actual query>
        #
        # This leaks the page URL and content into our router and forces
        # web_fetch on every follow-up. Strip everything up to and
        # including the closing </context> tag and keep only the real
        # user query from the tail.
        if "</context>" in last_user_text and "<context>" in last_user_text:
            tail = last_user_text.split("</context>", 1)[1]
            # Trim bullet / whitespace prefixes: "*   ", "- ", "• ", leading blanks
            tail = re.sub(r"^[\s\*\-\u2022]+", "", tail).strip()
            if tail:
                last_user_text = tail

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

        # Phase 11 — presentation wins over doc_qa/long_doc when the user
        # explicitly asks for slides. Runs BEFORE other short-circuits so an
        # attached PDF doesn't get routed into doc_qa.
        if _looks_like_presentation(detected.last_user_text or ""):
            # On follow-up turns without a fresh attachment (e.g.
            # "добавь картинки" after a previous presentation was generated),
            # the new user message alone doesn't carry the topic, and
            # pptx-service would produce a dec about nothing in particular.
            # Capture the last few turns as conversation context so the
            # schema model can understand what is being refined.
            conv_ctx = ""
            if not detected.document_attachments and messages and len(messages) > 1:
                ctx_parts: list[str] = []
                for m in messages[-6:]:
                    role = (m or {}).get("role", "")
                    if role not in ("user", "assistant"):
                        continue
                    c = m.get("content", "")
                    if isinstance(c, list):
                        c = " ".join(
                            p.get("text", "") for p in c
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    if not isinstance(c, str):
                        continue
                    # Strip pipe decoration from prior assistant turns so the
                    # pptx-service LLM doesn't mistake routing blocks / file
                    # markers for content to put in slides.
                    if role == "assistant":
                        c = self._scrub_assistant_history(c)
                    c = c.strip()
                    if not c:
                        continue
                    ctx_parts.append(f"[{role}] {c[:600]}")
                if ctx_parts:
                    conv_ctx = "\n".join(ctx_parts)
            return [
                SubTask(
                    kind="presentation",
                    input_text=detected.last_user_text or "",
                    attachments=list(detected.document_attachments or []),
                    model="",  # pptx-service chooses the schema model
                    metadata={
                        "lang": detected.lang,
                        "user_id": user_id,
                        "rule": "presentation_marker",
                        "conversation_context": conv_ctx,
                    },
                )
            ]

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

        # Skip doc_qa when the user's current message has an explicit
        # non-document intent (image generation, URL fetch, real-time info,
        # or memory recall). Otherwise, a file attached many turns ago
        # (which still lives in body["files"]) forces every follow-up
        # into doc_qa and blocks the real intent. Weather/news/rates
        # specifically: "какая погода в москве" after an unrelated PDF
        # upload used to run doc_qa + web_search in parallel and the
        # aggregator would emit both "в документах нет инфы о погоде" and
        # the real weather — the doc_qa half is pure noise.
        _url_dominant = bool(detected.urls) and (
            len(_URL_RE.sub("", detected.last_user_text or "").strip()) <= 60
        )
        _text = detected.last_user_text or ""
        _skip_doc_qa_for_intent = (
            detected.wants_image_gen
            or detected.wants_web_search
            or _url_dominant
            or self._looks_like_web_search(_text)
            or self._looks_like_memory_recall(_text, messages)
        )

        if detected.has_document and not _skip_doc_qa_for_intent:
            # Include current document filename so doc_qa can focus on it
            doc_names = [
                a.get("filename") or a.get("name") or "document"
                for a in detected.document_attachments
            ]
            plan.append(
                SubTask(
                    kind="doc_qa",
                    input_text=detected.last_user_text or "Резюмируй документ.",
                    attachments=detected.document_attachments,
                    model="mws/glm-4.6",
                    metadata={
                        "lang": detected.lang,
                        "user_id": user_id,
                        "doc_names": doc_names,
                    },
                )
            )

        # URL short-circuit — guarded two ways:
        # 1. Skip when a document/image/audio is attached: URLs in that case
        #    come from RAG-injected citations (GitHub links inside a PDF,
        #    accumulated sources from prior turns etc.), not user intent.
        # 2. Skip when the URL is incidental inside longer conversational text
        #    — only force web_fetch when the URL dominates the message (bare
        #    link with brief framing like "что здесь?"). Otherwise every
        #    follow-up question in a chat that once cited a URL gets
        #    force-routed to web_fetch.
        # Fire web_fetch when the message is URL-dominant. Previously we
        # skipped it whenever an attachment was present (to dodge RAG-
        # injected GitHub links from PDFs), but that also blocked legit
        # URL questions on chats with a stale document. Now the short
        # `non_url_text <= 60` gate alone is enough: a long document
        # answer that happens to contain a URL won't trip it.
        if detected.urls:
            non_url_text = _URL_RE.sub("", detected.last_user_text).strip()
            if len(non_url_text) <= 60:
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
                    model="mws/qwen-image-lightning",
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

        # Detect follow-up / context-referencing questions that don't need a
        # subagent — the aggregator can answer them directly from chat history.
        # Examples: "на русском", "переведи", "о чём было первое сообщение",
        # "расскажи подробнее", "предыдущий ответ".
        if len(messages) > 2 and self._is_context_followup(detected.last_user_text):
            # Return empty plan — aggregator will use conversation history
            return []

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

        # Pure text — call LLM classifier (pass recent messages for context)
        kind, model, time_window = await self._llm_classify(detected, messages)

        # Post-classifier safety net: if the classifier missed memory_recall
        # (due to error, timeout, or weak model), but the text semantically
        # refers to past conversations — override.  This check lives HERE
        # (not inside _llm_classify) so it runs even when the classifier
        # throws an exception and returns fallback.
        if kind != "memory_recall" and self._looks_like_memory_recall(
            detected.last_user_text, messages
        ):
            kind = "memory_recall"

        # Same pattern for web_search: if the classifier missed a real-time
        # info question (weather/news/rates/scores), override via word-group
        # intersection safety net. Common failure mode: 20b classifier gets
        # distracted by prior conversation context and returns ru_chat/general
        # for a fresh weather question.
        if kind not in ("web_search", "memory_recall") and self._looks_like_web_search(
            detected.last_user_text
        ):
            kind = "web_search"
            model = "mws/kimi-k2"

        # If routed to memory_recall but no time_window from classifier,
        # try to extract one from the user text.
        if kind == "memory_recall" and not time_window:
            time_window = self._extract_time_window(detected.last_user_text)

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

    @staticmethod
    def _is_context_followup(text: str) -> bool:
        """Detect if the user message is a follow-up that references conversation
        context (previous answers, translation requests, clarifications).
        These don't need a subagent — the aggregator handles them from history."""
        t = (text or "").lower().strip()
        if not t or len(t) > 300:
            return False
        _FOLLOWUP_PATTERNS = [
            # Russian
            r"предыдущ",        # предыдущий ответ
            r"на русском",
            r"на английском",
            r"переведи",
            r"перефразируй",
            r"повтори",
            r"подробнее",
            r"первое сообщение",
            r"первый вопрос",
            r"о чём (мы|было|был[аи]?)",
            r"что (мы|я) (спрашивал|говорил|обсуждал|писал)",
            r"в этой сессии",
            r"в этом (чате|диалоге|разговоре)",
            r"ранее",
            r"выше",
            # English
            r"previous (answer|response|message)",
            r"in (russian|english)",
            r"translate",
            r"rephrase",
            r"repeat",
            r"more detail",
            r"first message",
            r"earlier in (this|our)",
            r"what did (we|i|you) (talk|discuss|say|ask)",
        ]
        return any(re.search(p, t) for p in _FOLLOWUP_PATTERNS)

    @staticmethod
    def _extract_time_window(text: str) -> Optional[dict]:
        """Extract a time window from user text based on common time markers."""
        t = (text or "").lower()
        today = datetime.now(timezone.utc).date()

        if "сегодня" in t or "today" in t:
            return {
                "from": f"{today}T00:00:00Z",
                "to": f"{today}T23:59:59Z",
            }
        if "вчера" in t or "yesterday" in t:
            d = today - timedelta(days=1)
            return {"from": f"{d}T00:00:00Z", "to": f"{d}T23:59:59Z"}
        if "позавчера" in t:
            d = today - timedelta(days=2)
            return {"from": f"{d}T00:00:00Z", "to": f"{d}T23:59:59Z"}
        if "прошлой неделе" in t or "неделю назад" in t or "last week" in t or "a week ago" in t:
            return {
                "from": f"{today - timedelta(days=7)}T00:00:00Z",
                "to": f"{today}T23:59:59Z",
            }
        if "прошлом месяце" in t or "месяц назад" in t or "last month" in t or "a month ago" in t:
            return {
                "from": f"{today - timedelta(days=30)}T00:00:00Z",
                "to": f"{today}T23:59:59Z",
            }
        # "N дней/недель назад"
        import re as _re
        m = _re.search(r"(\d+)\s+(дн|день|дня|дней)\s+назад", t)
        if m:
            d = today - timedelta(days=int(m.group(1)))
            return {"from": f"{d}T00:00:00Z", "to": f"{d}T23:59:59Z"}
        m = _re.search(r"(\d+)\s+(недел)\S*\s+назад", t)
        if m:
            d = today - timedelta(weeks=int(m.group(1)))
            return {"from": f"{d}T00:00:00Z", "to": f"{today}T23:59:59Z"}
        return None

    _CONV_MARKERS = (
        "говорили", "разговаривали", "обсуждали", "общались",
        "беседовали", "разговор", "диалог", "беседа", "беседу",
        "обсуждение", "переписк", "чат", "чате",
        "рассказывал", "писал", "спрашивал", "отвечал",
        "discuss", "talk", "chat", "conversation", "said", "told",
        "речь", "тем", "рассказал",
    )
    _TIME_MARKERS = (
        "вчера", "позавчера", "сегодня", "неделю", "месяц",
        "раньше", "ранее", "прошл", "назад", "до этого",
        "помнишь", "вспомни", "напомни", "забыл",
        "yesterday", "today", "last week", "ago", "remember",
        "earlier", "before", "previous", "prior",
        "был ", "было ", "были ", "была ",
    )

    @classmethod
    def _looks_like_memory_recall(cls, text: str, messages: list | None = None) -> bool:
        """Semantic check: does the text refer to past conversations?

        Uses word-group intersection, NOT a single regex pattern.
        The idea: if the text contains BOTH a "conversation" word AND a
        "past reference" word, it's almost certainly memory_recall.

        Also handles follow-ups: if a recent user message in the conversation
        was about memory recall (had conv markers), and the current message
        has a time marker (e.g. "а вчера?"), treat it as memory_recall.
        """
        t = (text or "").lower()
        has_conv = any(m in t for m in cls._CONV_MARKERS)
        has_time = any(m in t for m in cls._TIME_MARKERS)
        # Direct "о чём мы..." / "о чём у нас..." pattern
        has_about_us = ("о чем мы" in t or "о чём мы" in t
                        or "о чем у нас" in t or "о чём у нас" in t
                        or "что мы" in t or "what did we" in t
                        or "do you remember" in t)

        if (has_conv and has_time) or has_about_us:
            return True

        # Follow-up detection: current message is short and has a time marker
        # (e.g. "а вчера?", "а на прошлой неделе?"), and a recent user message
        # in the conversation was about memory recall.
        if has_time and messages and len(t) < 50:
            for m in reversed(messages[:-1]):  # skip current message
                if m.get("role") != "user":
                    continue
                prev = m.get("content", "")
                if isinstance(prev, list):
                    prev = " ".join(
                        p.get("text", "") for p in prev
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                prev = (prev or "").lower()
                prev_conv = any(mk in prev for mk in cls._CONV_MARKERS)
                prev_about = ("о чем мы" in prev or "о чём мы" in prev
                              or "о чем у нас" in prev or "о чём у нас" in prev
                              or "что мы" in prev or "what did we" in prev)
                if prev_conv or prev_about:
                    return True
                break  # only check the most recent user message

        return False

    # Word groups for web_search safety net.
    # STRONG markers: inherently real-time, fire on their own (no time marker
    # needed). "погода" almost always means "current weather"; "новости" means
    # "recent news"; "последние" as a bare adjective is a strong signal too.
    _WEB_STRONG_MARKERS = (
        "погод", "weather", "прогноз погод",
        "новост", "news", "latest news", "последние",
        "что нового", "what's new", "whats new",
    )
    # TOPIC markers: volatile facts that MIGHT be historical (prices can be
    # queried for a past date). Need a real-time marker to fire.
    _WEB_TOPIC_MARKERS = (
        "температур",
        "курс", "exchange rate", "rate of", "цена", "цены", "стоимост", "price", "prices",
        "событи",
        "счёт", "счет", "матч", "score", "won", "выиграл", "проиграл",
        "рейс", "flight", "пробк", "traffic",
        "акци", "stock", "биткоин", "bitcoin", "btc",
        "дожд", "снег", "ветер", "давление",
    )
    # Real-time markers: "now / today / current" in both languages.
    _WEB_NOW_MARKERS = (
        "сегодня", "сейчас", "актуальн", "текущ", "нынешн",
        "today", "now", "current", "latest", "right now", "at the moment",
    )

    @classmethod
    def _looks_like_web_search(cls, text: str) -> bool:
        """Semantic check: does the text ask for real-time/current info?

        Word-group intersection (NOT a primary gate — runs AFTER the LLM
        classifier as a safety net, same pattern as _looks_like_memory_recall).
        Fires when the text contains BOTH a "volatile topic" word AND a
        "real-time" marker — e.g. "какая сегодня погода" (погода + сегодня),
        "курс доллара сейчас" (курс + сейчас). Without a time marker, a bare
        "курс доллара" is ambiguous and falls through to the LLM classifier.
        """
        t = (text or "").lower()
        if not t:
            return False
        if any(m in t for m in cls._WEB_STRONG_MARKERS):
            return True
        has_topic = any(m in t for m in cls._WEB_TOPIC_MARKERS)
        has_now = any(m in t for m in cls._WEB_NOW_MARKERS)
        return has_topic and has_now

    async def _llm_classify(
        self, detected: DetectedInput, messages: list | None = None
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
            "presentation, memory_recall, web_search. "
            "Valid primary_model: mws/gpt-alpha, mws/qwen3-235b, mws/qwen3-coder, "
            "mws/deepseek-r1-32b, mws/glm-4.6, mws/kimi-k2, mws/llama-3.1-8b. "
            "\n\nCRITICAL RULE — presentation:\n"
            "If the user asks you to create a presentation / slides / deck / pptx / "
            "powerpoint / представление / презентацию / слайды — intent MUST be "
            'presentation. Return {"intents":["presentation"], ...} EVEN IF a document '
            "is attached — do NOT add doc_qa or long_doc in that case. Examples:\n"
            '  "Сделай презентацию про async/await" → {"intents":["presentation"]}\n'
            '  "Вот резюме. Сделай из него презентацию." (has_document=true) → {"intents":["presentation"]}\n'
            '  "Make a 5-slide deck about Python" → {"intents":["presentation"]}\n'
            "\nCRITICAL RULE — web_search:\n"
            "If the request needs REAL-TIME or CURRENT information that the model "
            "cannot know from training data — intent MUST be web_search, regardless "
            "of whether the user said 'найди'/'поищи'/'search'. This covers ALL "
            "semantic variations. Triggers include: weather ('какая погода', "
            "'what's the weather'), news and current events ('что случилось', "
            "'latest news'), exchange rates and prices ('курс доллара', 'цена "
            "биткоина', 'stock price'), sports scores, flight/traffic status, "
            "'кто выиграл', 'who won', 'что сейчас с X', and any question with "
            "'сегодня'/'сейчас'/'today'/'now'/'current' about facts outside the "
            "model's knowledge. Do NOT classify these as ru_chat/general — the "
            "model will hallucinate or refuse. Examples: "
            "'какая сегодня погода в москве' -> web_search; "
            "'узнай курс доллара' -> web_search; "
            "'что нового с SpaceX' -> web_search; "
            "'кто выиграл матч вчера' -> web_search. "
            "Use primary_model mws/kimi-k2 for web_search.\n\n"
            "CRITICAL RULE — memory_recall:\n"
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
            '"о чём был разговор вчера" -> {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-04-11T00:00:00Z","to":"2026-04-11T23:59:59Z"}}; '
            '"о чём мы сегодня разговаривали" -> {"intents":["memory_recall"],"lang":"ru","time_window":{"from":"2026-04-12T00:00:00Z","to":"2026-04-12T23:59:59Z"}}.'
        )
        # Build classifier input: include recent conversation context so
        # the classifier can understand follow-up messages like "а вчера?"
        # after a memory_recall question.
        classifier_msgs: list[dict] = [{"role": "system", "content": system}]
        if messages and len(messages) > 1:
            # Include last few turns (up to 6 messages) for context,
            # but truncate each to keep token usage low.
            recent = messages[-6:]
            for m in recent[:-1]:  # all except last (added separately)
                role = m.get("role", "user")
                text = m.get("content", "")
                if isinstance(text, list):
                    text = " ".join(
                        p.get("text", "") for p in text
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                if role in ("user", "assistant") and text.strip():
                    classifier_msgs.append(
                        {"role": role, "content": text[:300]}
                    )
        classifier_msgs.append(
            {"role": "user", "content": detected.last_user_text[:2000]}
        )

        try:
            resp = await self._call_litellm(
                model=self.valves.classifier_model,
                messages=classifier_msgs,
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
            "web_search": "web_search",
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
            # Log a concise failure line even when debug is off — upstream
            # timeouts/transport errors are otherwise invisible.
            print(
                f"[mws-auto {trace_id[:8]}] sa_{task.kind} FAILED: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            return CompactResult(kind=task.kind, error=f"{type(e).__name__}: {e}")

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
        # Build synthetic DetectedInput from transcript.
        # Preserve the ORIGINAL request language (detected.lang) when the user
        # wrote an explicit prompt like "Summarize this audio".  Only override
        # to transcript language when the user sent no text (audio-only).
        synth = DetectedInput(last_user_text=transcript, lang=detected.lang)
        if not detected.last_user_text.strip():
            # Audio-only (no user text) — infer lang from transcript
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
        # Extract memory context injected by the mws_memory inlet filter.
        # It arrives as one or more role=system messages; without this we
        # would silently drop them when building final_messages below.
        memory_ctx = self._extract_memory_context(messages)
        memory_block = (
            f"\n\n--- CONTEXT FROM USER MEMORY ---\n{memory_ctx}\n"
            "Используй эти факты, чтобы отвечать на вопросы о пользователе "
            "(имя, работа, предпочтения). Не ссылайся на сам факт наличия памяти.\n"
            if memory_ctx else ""
        )
        system_prompt = (
            'Ты — финальный агент "MWS GPT Auto". Ниже — результаты работы вспомогательных '
            "субагентов. Используй их как факты. Не показывай пользователю внутреннюю "
            "кухню и не дублируй служебные теги вроде [sa_*]. "
            "Если среди результатов есть ошибки — кратко упомяни, но продолжи отвечать. "
            "Если у субагентов есть artifacts (сгенерированные файлы или изображения), "
            "ссылка на них будет автоматически добавлена после твоего текста. "
            "НИКОГДА не вставляй markdown-ссылки на изображения (![...](...)) и на файлы "
            "([имя.pptx](...)) — их добавит система. НЕ пиши фраз вида "
            "«изображение сгенерировано» или «файл приложён»: только краткое содержательное "
            "резюме того, что было сделано (тема, число слайдов и т.п.). "
            "НИКОГДА не упоминай и не воспроизводи символ 📎 и блоки "
            "<details>🎯 Routing decision</details> — это служебная разметка, "
            "которую добавляет система. Даже если видишь их в истории диалога, "
            "не копируй их в свой ответ и не выдумывай похожие. "
            "НЕ упоминай имена файлов из предыдущих ответов — в истории они "
            "могут быть устаревшими; имя реального артефакта этого хода "
            "подставит система. "
            "Если в результатах субагентов есть 'Citations:', обязательно вставь "
            "ссылки в текст как пронумерованные цитаты [1], [2], [3] и перечисли "
            "их в конце ответа в формате: [1] URL, [2] URL и т.д. "
            "ВАЖНО: ты получаешь полную историю диалога. Если пользователь спрашивает "
            "о предыдущих сообщениях, содержании беседы, или просит переформулировать/"
            "перевести предыдущий ответ — используй историю диалога, а НЕ результаты "
            "субагентов. Результаты субагентов полезны только для нового контента "
            "(поиск, fetch, генерация). "
            f"{lang_instr}"
            f"{memory_block}"
            f"\n\n--- SUBAGENT RESULTS ---\n{scratchpad}"
        )

        # Pass conversation history so the aggregator understands context
        # (e.g. "translate previous answer", "now in Russian", follow-ups)
        final_messages = [{"role": "system", "content": system_prompt}]
        # Include up to last 10 messages (user + assistant turns) for context
        history = (messages or [])[-10:]
        for msg in history:
            role = (msg or {}).get("role", "")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            if isinstance(content, list):
                # Flatten multimodal content to text only
                text_parts = [
                    i.get("text", "")
                    for i in content
                    if isinstance(i, dict) and i.get("type") == "text"
                ]
                content = "\n".join(text_parts)
            if isinstance(content, str) and content.strip():
                if role == "assistant":
                    content = self._scrub_assistant_history(content)
                    if not content.strip():
                        continue
                final_messages.append({"role": role, "content": content})

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

    def _extract_memory_context(self, messages: list) -> str:
        """Pull memory-injection text out of system messages added by the
        `mws_memory` inlet filter.  The filter prepends lines starting with
        'What you know about this user' — we keep everything from that
        marker to the end of the system block."""
        marker = "What you know about this user"
        for msg in messages or []:
            if (msg or {}).get("role") != "system":
                continue
            content = msg.get("content")
            if not isinstance(content, str) or marker not in content:
                continue
            idx = content.find(marker)
            return content[idx:].strip()
        return ""

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

    # ------------------------------------------------------------------
    # Assistant-history scrubber
    # ------------------------------------------------------------------
    #
    # Prior assistant turns contain two kinds of pipe-generated decoration
    # that the aggregator tends to copy on follow-ups: the <details>🎯 Routing
    # decision</details> block yielded at the start of every pipe call, and
    # the 📎 markdown link appended by _render_artifacts. When the aggregator
    # sees them in its history it reproduces them in the next response
    # (sometimes with hallucinated filenames pulled from other contexts —
    # that is what caused the `strategiya_razvitiya_2024.pptx` ghost).
    # Strip them both from history AND from the aggregator's own output.

    _DETAILS_BLOCK_RE = re.compile(
        r"<details>[\s\S]*?</details>\s*", re.IGNORECASE
    )
    _PAPERCLIP_LINE_RE = re.compile(r"(?m)^\s*📎[^\n]*$")
    _FILE_LINK_RE = re.compile(
        r"\[[^\]]*\]\((?:/api/v1/files/[^)]+|[^)]*\.pptx[^)]*)\)"
    )
    _IMAGE_LINK_RE = re.compile(r"!\[[^\]]*\]\([^\)]+\)")

    @classmethod
    def _scrub_assistant_history(cls, content: str) -> str:
        """Remove pipe-generated decoration from a prior assistant message
        before it is passed back to the aggregator as history. Keeps the
        actual natural-language part of the reply intact."""
        if not isinstance(content, str) or not content:
            return content
        content = cls._DETAILS_BLOCK_RE.sub("", content)
        content = cls._FILE_LINK_RE.sub("", content)
        content = cls._IMAGE_LINK_RE.sub("", content)
        content = cls._PAPERCLIP_LINE_RE.sub("", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        return content

    @classmethod
    def _scrub_artifact_echoes(cls, text: str) -> str:
        """Post-stream cleanup of the aggregator's own output when artifacts
        are present: strip hallucinated image links, file links, 📎 lines and
        any routing-decision block the model may have mirrored from history."""
        if not text:
            return text
        text = cls._DETAILS_BLOCK_RE.sub("", text)
        text = cls._IMAGE_LINK_RE.sub("", text)
        text = cls._FILE_LINK_RE.sub("", text)
        text = cls._PAPERCLIP_LINE_RE.sub("", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    def _render_artifacts(self, results: list[CompactResult]) -> str:
        out: list[str] = []
        for r in results:
            for art in r.artifacts or []:
                t = art.get("type")
                url = art.get("url")
                if not url:
                    continue
                if t == "image":
                    out.append(f"![generated]({url})")
                elif t == "file":
                    name = art.get("filename") or "file"
                    out.append(f"\n📎 [{name}]({url})")
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

        # 1. Local file path (injected by inlet filter from OpenWebUI uploads)
        local_path = att.get("path", "")
        if local_path:
            import os
            if os.path.isfile(local_path):
                try:
                    with open(local_path, "rb") as fp:
                        audio_bytes = fp.read()
                except Exception as e:
                    return CompactResult(kind="stt", error=f"read local file: {e}")
        # 2. Base64-encoded data
        if not audio_bytes and att.get("data"):
            try:
                audio_bytes = base64.b64decode(att["data"])
            except Exception as e:
                return CompactResult(kind="stt", error=f"bad base64: {e}")
        # 3. URL download
        if not audio_bytes and att.get("url"):
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
            # Lightning variant renders in ~10–20s on the MWS GPT upstream;
            # the heavy `mws/qwen-image` routinely exceeds 120s and times
            # out the pipe, silently killing the artifact.
            "model": task.model or "mws/qwen-image-lightning",
            "prompt": task.input_text,
            "n": 1,
            "size": "1024x1024",
        }
        # 45s is the point past which users notice. The Lightning model
        # comfortably fits under this when upstream is healthy; when it
        # is not, failing fast with a clear error beats making the user
        # wait minutes for nothing.
        async with httpx.AsyncClient(timeout=45) as cli:
            try:
                r = await cli.post(
                    url, json=payload, headers=self._auth_headers()
                )
            except httpx.ReadTimeout:
                return CompactResult(
                    kind="image_gen",
                    error=(
                        "image gen timed out after 45s — the MWS image "
                        "backend is currently overloaded. Try again in a "
                        "minute."
                    ),
                )
            except httpx.HTTPError as e:
                return CompactResult(
                    kind="image_gen", error=f"image gen transport: {e}"
                )
            if r.status_code != 200:
                return CompactResult(
                    kind="image_gen",
                    error=f"image gen HTTP {r.status_code}: {r.text[:200]}",
                )
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
        # Wikipedia: use REST API to avoid 403 scraping blocks
        wiki_match = re.match(
            r"https?://(\w+)\.wikipedia\.org/wiki/(.+)", url
        )
        if wiki_match:
            lang, article = wiki_match.group(1), wiki_match.group(2)
            api_url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{article}"
            headers = {
                "User-Agent": "MWS-GPT-Hub/1.0 (https://gpt.mws.ru; admin@mws.ru)"
            }
            async with httpx.AsyncClient(
                timeout=15, headers=headers, follow_redirects=True
            ) as cli:
                r = await cli.get(api_url)
                if r.status_code == 200:
                    data = r.json()
                    return data.get("extract", "")[:6000]
                # Fall through to regular fetch if API fails

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        async with httpx.AsyncClient(
            timeout=15, headers=headers, follow_redirects=True
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
        """Lightweight DuckDuckGo Lite search. Returns [{title,url,snippet}]."""
        url = "https://lite.duckduckgo.com/lite/"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=15, headers=headers, follow_redirects=True
        ) as cli:
            r = await cli.post(url, data={"q": query})
            r.raise_for_status()
            html = r.text
        # DDG Lite: each result is a pair of <tr> rows:
        #   <a class='result-link' href="...">title</a>
        #   <td class='result-snippet'>snippet text</td>
        import html as _html

        results: list[dict] = []
        links = list(
            re.finditer(
                r"<a[^>]+href=['\"]([^'\"]+)['\"][^>]+class=['\"]result-link['\"][^>]*>(.*?)</a>",
                html,
                re.DOTALL,
            )
        )
        snippets = list(
            re.finditer(
                r"<td[^>]+class=['\"]result-snippet['\"][^>]*>(.*?)</td>",
                html,
                re.DOTALL,
            )
        )
        for i, link_m in enumerate(links):
            href = link_m.group(1).strip()
            title = re.sub(r"<[^>]+>", "", link_m.group(2)).strip()
            title = _html.unescape(title)
            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i].group(1)).strip()
                snippet = _html.unescape(snippet)
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

        # Focus the LLM on the specific document(s) from this message
        doc_names = task.metadata.get("doc_names") or []
        focus_hint = ""
        if doc_names:
            names_str = ", ".join(f'"{n}"' for n in doc_names)
            focus_hint = (
                f" Документ(ы) из текущего сообщения: {names_str}. "
                "Отвечай ТОЛЬКО по этому документу, игнорируй контекст "
                "из других документов/источников."
            )
        resp = await self._call_litellm(
            model=task.model or "mws/glm-4.6",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Отвечай на вопрос по предоставленному документу. "
                        "Цитируй разделы/страницы, если указаны. "
                        "Если ответа в документе нет — скажи прямо."
                        + focus_hint
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

    # ------------------------------------------------------------------
    # Phase 11 — presentation subagent
    # ------------------------------------------------------------------

    _PPTX_MIME = (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )

    _TRANSLIT_MAP = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }

    @classmethod
    def _slug(cls, text: str) -> str:
        t = (text or "").strip().lower()
        out: list[str] = []
        for ch in t:
            if ch in cls._TRANSLIT_MAP:
                out.append(cls._TRANSLIT_MAP[ch])
            elif ch.isascii() and (ch.isalnum() or ch in " _-"):
                out.append(ch)
            else:
                out.append(" ")
        slug = re.sub(r"\W+", "_", "".join(out)).strip("_")
        return slug[:60] or "presentation"

    async def _upload_to_owui_files(
        self, content: bytes, filename: str, mime: str
    ) -> Optional[dict]:
        """POST the bytes to OpenWebUI Files API. Returns the JSON payload
        (with `id`) on success, `None` on any failure (missing token, non-200,
        transport error). Never raises — the caller decides on fallback."""
        token = os.getenv("OWUI_ADMIN_TOKEN", "").strip()
        if not token:
            # Fallback: bootstrap sidecar writes the auto-provisioned admin
            # token here after first signup (shared bind-mount from host
            # ./data/secrets). This makes pptx delivery work on a zero-config
            # `docker compose up` without the operator ever touching .env.
            try:
                token = pathlib.Path("/owui_secrets/owui_admin_token").read_text(encoding="utf-8").strip()
            except Exception:
                token = ""
        if not token:
            if self.valves.debug:
                print("[mws-auto] owui_upload: OWUI_ADMIN_TOKEN not set")
            return None
        url = "http://localhost:8080/api/v1/files/"
        headers = {"Authorization": f"Bearer {token}"}
        files = {"file": (filename, content, mime)}
        try:
            async with httpx.AsyncClient(timeout=60) as cli:
                r = await cli.post(url, files=files, headers=headers)
        except Exception as e:
            if self.valves.debug:
                print(f"[mws-auto] owui_upload transport error: {e}")
            return None
        if r.status_code != 200:
            if self.valves.debug:
                print(f"[mws-auto] owui_upload HTTP {r.status_code}: {r.text[:200]}")
            return None
        try:
            return r.json()
        except Exception:
            return None

    async def _sa_presentation(self, task: SubTask) -> CompactResult:
        """Phase 11 subagent: delegate pptx generation to pptx-service and
        upload the result into OpenWebUI's Files API. Falls back to a
        markdown slide plan on any error."""
        # 1. Collect source bytes (first readable attachment on disk)
        src_bytes: Optional[bytes] = None
        src_name = ""
        src_mime = "application/octet-stream"
        for att in (task.attachments or []):
            path = att.get("path") or ""
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fp:
                    src_bytes = fp.read()
                src_name = (
                    att.get("filename")
                    or att.get("name")
                    or os.path.basename(path)
                    or "source.bin"
                )
                src_mime = att.get("content_type") or "application/octet-stream"
                break
            except Exception as e:
                if self.valves.debug:
                    print(f"[mws-auto] presentation: read attachment failed: {e}")

        if src_bytes is not None and len(src_bytes) > 20 * 1024 * 1024:
            return await self._presentation_text_fallback(
                task, reason="attachment > 20 MB; use a smaller source"
            )

        # 2. Call pptx-service
        build_url = "http://pptx-service:8000/build"
        data = {"user_instruction": task.input_text or "Сделай презентацию."}
        # Follow-up without attachment: pass the conversation context as
        # source_text so the schema LLM understands what we're refining.
        conv_ctx = (task.metadata or {}).get("conversation_context") or ""
        if src_bytes is None and conv_ctx:
            data["source_text"] = conv_ctx[:8000]
        files = None
        if src_bytes is not None:
            files = {"file": (src_name, src_bytes, src_mime)}
        try:
            async with httpx.AsyncClient(timeout=240) as cli:
                r = await cli.post(build_url, data=data, files=files)
        except Exception as e:
            return await self._presentation_text_fallback(
                task, reason=f"pptx-service unreachable: {e}"
            )
        if r.status_code != 200:
            return await self._presentation_text_fallback(
                task, reason=f"pptx-service HTTP {r.status_code}: {r.text[:200]}"
            )
        pptx_bytes = r.content
        title = "Presentation"
        title_b64 = r.headers.get("X-Title-B64") or ""
        if title_b64:
            try:
                title = base64.b64decode(title_b64).decode("utf-8") or title
            except Exception as e:
                if self.valves.debug:
                    print(f"[mws-auto] presentation title decode error: {e}")
        slide_count = r.headers.get("X-Slide-Count") or "?"

        # 3. Upload to OWUI Files API
        safe_name = self._slug(title) + ".pptx"
        uploaded = await self._upload_to_owui_files(
            pptx_bytes, safe_name, self._PPTX_MIME
        )
        if not uploaded or not uploaded.get("id"):
            return await self._presentation_text_fallback(
                task,
                reason="OWUI_ADMIN_TOKEN not set or upload failed",
                prefix=(
                    "⚠️ Файл .pptx сгенерирован, но не может быть приложен к чату "
                    "(не задан OWUI_ADMIN_TOKEN или сбой загрузки). Ниже — план слайдов.\n\n"
                ),
            )

        file_id = uploaded["id"]
        return CompactResult(
            kind="presentation",
            summary=f"Готова презентация «{title}» — {slide_count} слайдов.",
            artifacts=[
                {
                    "type": "file",
                    "url": f"/api/v1/files/{file_id}/content",
                    "filename": safe_name,
                    "mime": self._PPTX_MIME,
                }
            ],
        )

    async def _presentation_text_fallback(
        self, task: SubTask, reason: str, prefix: str = ""
    ) -> CompactResult:
        """Ask the default long-doc model to produce a markdown slide plan so the
        user still gets useful output when the pptx pipeline fails."""
        if self.valves.debug:
            print(f"[mws-auto] presentation fallback: {reason}")
        lang = (task.metadata or {}).get("lang", "en")
        sys = (
            "Ты эксперт по созданию структур презентаций. Составь план на 5–8 слайдов в "
            "markdown: заголовок слайда (## Slide N — …) и 3–5 буллетов. На русском."
            if lang == "ru"
            else "You design presentation outlines. Produce 5–8 slides in markdown: "
                 "heading `## Slide N — …` plus 3–5 bullets. Keep it concise."
        )
        try:
            result = await self._text_subagent(
                model="mws/glm-4.6",
                system=sys,
                task=task,
                temperature=0.5,
            )
            summary = prefix + (result.summary or "")
            return CompactResult(
                kind="presentation",
                summary=self._truncate_tokens(summary, 700),
                metadata={"fallback_reason": reason},
            )
        except Exception as e:
            return CompactResult(
                kind="presentation",
                summary=prefix + f"(не удалось сгенерировать план: {e})",
                error=reason,
            )
