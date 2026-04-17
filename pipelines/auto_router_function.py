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
    wants_deep_research: bool = False

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


# Phase 12 — FactChecker dataclasses.
# These are produced by _sa_fact_check (added in phase-12-6) and carried in
# CompactResult.metadata["report"] so the aggregator can cite verdicts without
# breaking the context-isolation invariant (only summaries cross boundaries).


@dataclass
class UrlStatus:
    url: str
    status: str  # url_ok | url_redirect | url_404 | url_unreachable | url_auth_required | url_blocked_ssrf
    http_code: Optional[int] = None
    final_url: Optional[str] = None
    snippet: str = ""  # first ~2KB of text when url_ok, fed to the verdict LLM
    error: str = ""


@dataclass
class Claim:
    text: str
    source_kind: str  # kind of the subagent whose summary produced this claim
    verdict: str = "unknown"  # grounded | partial | ungrounded | unknown
    evidence_url: Optional[str] = None
    reason: str = ""


@dataclass
class FactCheckReport:
    urls: list[UrlStatus] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    total_checked_kinds: list[str] = field(default_factory=list)
    error: Optional[str] = None


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
# Matches any 4-digit year 2000-2099. Used by _looks_like_web_search together
# with an info-seeking verb: if the user asks about a year >= current, the
# model's training data is almost certainly stale/partial and we must hit DDG.
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
# Deep research: multi-step investigation requiring multiple parallel searches
# plus synthesis. Separate from web_search because the classifier needs to
# know whether to dispatch _sa_deep_research (heavier, 3-5 DDG queries + fetch
# + synthesis via kimi-k2) instead of a single-shot _sa_web_search.
_DEEP_RESEARCH_RE = re.compile(
    r"(?iu)("
    r"проведи\s+(глубок\w*|подробн\w*|детальн\w*|тщательн\w*|полн\w*)?\s*исследовани\w*|"
    r"исследуй\s+тему|исследуй\s+вопрос|"
    r"(глубок\w*|подробн\w*|детальн\w*)\s+исследовани\w*|"
    r"углуб\w*\s+(в\s+тему|анализ)|"
    r"всесторонн\w*\s+анализ|"
    r"составь\s+(аналитическ\w*\s+)?отчёт|"
    r"deep\s+research|"
    r"in[-\s]?depth\s+(research|analysis|investigation)|"
    r"thorough\s+(research|analysis|investigation)|"
    r"research\s+(the\s+)?(topic|question|subject)"
    r")"
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


# Phase 12 — FactChecker constants.
# _CHECKABLE_KINDS: if the plan contains any of these subagent kinds, the
# phase-1.5 fact-check runs automatically. The other kinds (general, ru_chat,
# code, reasoner, long_doc, vision, stt, image_gen, presentation) either make
# no verifiable real-world claims or the cost of verification doesn't pay off.
_CHECKABLE_KINDS = {
    "web_search",
    "web_fetch",
    "deep_research",
    "memory_recall",
    "doc_qa",
}

# Explicit user trigger: "проверь факты / fact-check / verify the sources" etc.
# Forces fact-check even when the plan doesn't include a _CHECKABLE_KINDS item.
_FACT_CHECK_TRIGGER_RE = re.compile(
    r"(?i)(провер\w*\s+(факты|источник\w*)|fact[-\s]?check|verify\s+(the\s+)?(claims|sources|facts))"
)

# SSRF guard: any URL that resolves (or claims to resolve) to localhost or
# RFC1918 private ranges is dropped before _validate_urls touches it. This
# blocks hallucinated or malicious URLs from probing internal services.
_SSRF_BLOCK_RE = re.compile(
    r"^(https?://)?(localhost|127\.|10\.|169\.254\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)"
)


# Phase 12-4 — prompt for the cheap LLM that extracts verifiable claims from
# the summaries of other subagents (fact_check_claim_model, default
# mws/gpt-oss-20b). Must stay in sync with `_extract_claims` below.
_CLAIM_EXTRACT_PROMPT = """Ты — выделитель проверяемых фактов.
На вход — сводки ответов от других AI-субагентов. Выдели утверждения,
которые МОЖНО проверить по внешним источникам (люди, организации,
события, даты, числа, адреса, URL, цитаты).

ЗАПРЕЩЕНО включать:
- общие мнения ("это важная тема")
- перефразирование вопроса пользователя
- утверждения без конкретики

КРИТИЧНО про язык:
- Пиши каждый claim НА ТОМ ЖЕ языке, на котором написано исходное
  summary. Русский summary → русские claims. Английский → английские.
- НЕ переводи на английский, НЕ транслитерируй имена.
- Это обязательное условие для последующего сравнения с текстом
  источников — любой перевод ломает attribution-проверку.

Верни JSON: {"claims": [{"text": "...", "source_kind": "..."}]}
Не более 6 claims. Формулируй каждый claim одним полным предложением.
"""

# Phase 12-5 — prompt for the attribution/grounding LLM (fact_check_model,
# default mws/gpt-oss-20b). This is NOT a truth-check: we only verify that
# each claim is traceable to a snippet that the subagent actually fetched.
# "The source may itself be wrong" is explicitly out of scope — we're
# catching subagent hallucinations, not auditing the internet.
_VERDICT_PROMPT = """Ты — проверяющий attribution (а не истинности).
Задача: для каждого утверждения определи, появляется ли оно в предоставленных
snippets (текстах реально полученных URL). Ты НЕ оцениваешь, правда ли это —
только взял ли субагент это из источника или выдумал.

Правила:
- grounded: утверждение (или его суть) явно присутствует в одном из snippets.
            ОБЯЗАТЕЛЬНО укажи evidence_url из списка доказательств.
- partial: в snippets есть только часть утверждения (тема/сущность совпадает,
           но ключевые детали — имена, числа, даты — отсутствуют).
           evidence_url можно указать, если совпадает тема.
- ungrounded: ни одно из snippets не содержит это утверждение и не упоминает
              его предмет. Источник не найден. evidence_url пустой.

ВАЖНО:
- Отсутствие утверждения в snippets — это ungrounded, а НЕ «ложь».
  Мы НЕ знаем, правда это или нет — мы знаем только, что субагент это ниоткуда не взял.
- НЕ выставляй grounded без evidence_url из списка доказательств.
- НЕ используй внешние знания — только текст snippets.

Утверждения пронумерованы как [1], [2], [3], ... В ответе обязательно укажи
тот же номер в поле "index" — по нему мы связываем вердикт с утверждением.

КРИТИЧНО: верни вердикт для КАЖДОГО утверждения из списка. Если в snippets
нет ни одного упоминания — вердикт `ungrounded` с пустым evidence_url, это
нормальный результат. Пустой объект {} или пустой список verdicts: [] —
это ОШИБКА, так возвращать нельзя ни при каких условиях.

Верни JSON строго в таком виде (N = число утверждений):
{"verdicts": [
  {"index": 1, "verdict": "grounded|partial|ungrounded", "evidence_url": "...", "reason": "..."},
  {"index": 2, "verdict": "...", "evidence_url": "...", "reason": "..."},
  ... до index = N
]}
Количество объектов в verdicts должно совпадать с числом утверждений.
"""


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
        # Phase 12 — FactChecker valves. The phase-1.5 fact-check stage runs
        # after the main asyncio.gather dispatch and before _stream_aggregate,
        # validating URLs and key claims from checkable subagents. See
        # PLAN_truth_agent.md for the activation rules and cost model.
        fact_check_enabled: bool = Field(
            default=True,
            description="Master switch for the phase-1.5 fact-check stage.",
        )
        fact_check_timeout: float = Field(
            default=60.0,
            description="Overall deadline (seconds) for the phase-1.5 attribution-check stage. 30s was not enough when the fallback retry to kimi-k2 fires on dense news snippets (primary gpt-oss-20b + kimi-k2 retry + URL validation + claim extraction all in the same budget). 60s covers the retry path without regressing the fast happy path.",
        )
        fact_check_max_urls: int = Field(
            default=12,
            description="Maximum number of URLs to validate per request.",
        )
        fact_check_max_claims: int = Field(
            default=6,
            description="Maximum number of claims to extract and verdict per request.",
        )
        fact_check_model: str = Field(
            default="mws/gpt-oss-20b",
            description="Model used to issue grounded/partial/ungrounded attribution verdicts.",
        )
        fact_check_claim_model: str = Field(
            default="mws/gpt-oss-20b",
            description="Cheap model used to extract checkable claims from summaries.",
        )
        fact_check_fallback_model: str = Field(
            default="mws/kimi-k2",
            description=(
                "Stronger model used as retry when the primary verdict LLM "
                "returns an empty JSON ({} or {\"verdicts\":[]}). gpt-oss-20b "
                "sometimes gives up on dense news-homepage snippets; kimi-k2 "
                "handles that kind of input better. Retry only fires on empty "
                "— not on parse/truncation errors, which are already salvaged."
            ),
        )
        fact_check_url_timeout: float = Field(
            default=15.0,
            description=(
                "Per-URL read timeout (seconds) inside _validate_urls. Was 6s "
                "— bumped to 15s to match _fetch_url_text so slow sites "
                "(Cloudflare challenges, geo latency) don't falsely come back "
                "as url_unreachable when the web_search subagent had read them fine."
            ),
        )

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
        __metadata__: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        messages = body.get("messages", []) or []
        files = body.get("files", []) or []
        trace_id = str(uuid.uuid4())
        user_id = (__user__ or {}).get("id")
        chat_id = (
            (__metadata__ or {}).get("chat_id")
            or body.get("chat_id")
            or ""
        )

        detected = self._detect(messages, files)
        if self.valves.debug:
            print(
                f"[mws-auto {trace_id[:8]}] detected={detected.to_dict()} "
                f"user_id={user_id} chat_id={chat_id}"
            )

        plan = await self._classify_and_plan(
            detected, messages, user_id=user_id, chat_id=chat_id
        )
        if self.valves.debug:
            print(
                f"[mws-auto {trace_id[:8]}] plan={[(t.kind, t.model) for t in plan]}"
            )

        yield self._format_routing_block(plan, detected, include_verifier=True)

        results = await self._dispatch(plan, trace_id=trace_id)

        # Post-process: if stt happened and no chat-subagent yet, re-plan from transcript
        results = await self._maybe_reclassify_stt(
            results, detected, messages, trace_id=trace_id, user_id=user_id
        )

        if self._should_fact_check(plan, detected):
            try:
                user_q = detected.last_user_text or ""
                fc = await self._sa_fact_check(results, detected, user_q)
                results.append(fc)
                if self.valves.debug:
                    print(f"[mws-auto {trace_id[:8]}] fact_check done: {fc.summary}")
            except Exception as e:
                print(f"fact_check phase FAILED: {type(e).__name__}: {e}")

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

        fc = next((r for r in results if r.kind == "fact_check"), None)
        if fc:
            if fc.error:
                yield (
                    "\n\n<details>\n<summary>⚠️ Проверка реальности источников</summary>\n\n"
                    f"Проверка не выполнена: `{fc.error}`.\n\n</details>"
                )
            else:
                report = (fc.metadata or {}).get("report") or {}
                details_md = self._render_fact_check_details(report)
                if details_md:
                    yield details_md

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

        # Normalize markdown decoration that OpenWebUI sometimes wraps around
        # user input after a RAG / web_search turn (observed: `*   _Докажи, что
        # …_`). Two issues this fixes:
        #   1. `_italic_` wrapping — `_` is a \w character in Python regex, so
        #      `\b` anchors in _REASONER_RE etc. don't fire against it.
        #   2. Leading bullet markers `*   ` / `- ` / `• ` that leak from the
        #      surrounding list context even without a `<context>` tag.
        last_user_text = re.sub(r"^[\s\*\-\u2022]+", "", last_user_text)
        last_user_text = last_user_text.strip().strip("_*`").strip()

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
            det.wants_deep_research = bool(_DEEP_RESEARCH_RE.search(det.last_user_text))

        return det

    # ------------------------------------------------------------------
    # Classifier + planner
    # ------------------------------------------------------------------

    async def _classify_and_plan(
        self,
        detected: DetectedInput,
        messages: list,
        user_id: Optional[str] = None,
        chat_id: Optional[str] = None,
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
            or detected.wants_deep_research
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

        # Deep research fires on its own signal ("проведи исследование",
        # "deep research", ...) OR when the user asks web_search AND explicitly
        # wants "подробно"/"детально"/"thoroughly". When both web_search and
        # deep_research trigger, prefer deep_research (it subsumes web_search —
        # runs 3-5 parallel DDG queries + synthesis instead of one).
        if detected.wants_deep_research:
            plan.append(
                SubTask(
                    kind="deep_research",
                    input_text=detected.last_user_text,
                    model="mws/kimi-k2",
                    metadata={"lang": detected.lang, "user_id": user_id},
                )
            )
        elif detected.wants_web_search:
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

        # Same pattern for code: gpt-oss-20b tends to return general/ru_chat for
        # Russian code requests ("напиши на Python класс …"), and the lang-aware
        # override then pins it to sa_ru_chat/qwen3-235b instead of sa_code.
        if kind not in ("code", "reasoner", "memory_recall", "web_search") and self._looks_like_code(
            detected.last_user_text
        ):
            kind = "code"
            model = "mws/qwen3-coder"

        # If routed to memory_recall but no time_window from classifier,
        # try to extract one from the user text.
        if kind == "memory_recall" and not time_window:
            time_window = self._extract_time_window(detected.last_user_text)

        meta: dict = {"lang": detected.lang, "user_id": user_id}
        if time_window:
            meta["time_window"] = time_window
        if kind == "memory_recall" and chat_id:
            # Exclude current chat from recall — "в прошлом чате" / "in the
            # previous chat" implies other chats, not the one we're in now.
            meta["exclude_chat_id"] = chat_id
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
        # Competition / event outcomes: inherently public-record facts the
        # classifier can't reliably know. "кто выиграл ...", "кто победил ...",
        # "кто стал чемпионом ..." — all real-time lookups.
        "кто выиграл", "кто победил", "кто стал", "кто получил",
        "кто занял", "кто выиграет", "кто победит",
        "who won", "who became", "who is the winner",
    )
    # Information-seeking verbs used by the year-based heuristic below.
    _WEB_SEEK_MARKERS = (
        "найди ", "найти ", "поищи ", "ищу ", "ищи ",
        "расскажи про", "расскажи о", "расскажи об",
        "узнай ", "узнать ",
        "что случилось", "что произошло", "что известно",
        "информаци",
        "find ", "search ", "look up", "tell me about",
        "what happened", "what is known",
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

    # Code safety-net markers. Same word-group pattern as _looks_like_web_search:
    # either a "strong" marker fires alone, or (lang marker + action marker) combo.
    # Runs AFTER _llm_classify as a safety net — gpt-oss-20b frequently returns
    # general/ru_chat for Russian code requests ("напиши на Python класс …"),
    # and the lang-aware override then pins the plan to sa_ru_chat/qwen3-235b
    # instead of sa_code/qwen3-coder.
    _CODE_STRONG_MARKERS = (
        "напиши код", "напиши функц", "напиши класс", "напиши метод",
        "напиши скрипт", "напиши программ", "напиши тест",
        "реализуй функц", "реализуй класс", "реализуй метод",
        "реализуй алгоритм", "реализуй структур",
        "unit-тест", "unit тест", "юнит-тест", "юнит тест",
        "докстринг", "докстрок", "отрефактор", "отлад",
        "pytest", "unittest", "jest", "vitest", "junit",
        "docstring", "type hints", "type annotations",
        "unit test", "unit-test", "write code", "write a function",
        "write a class", "write a method", "write a program",
        "write a script", "write tests", "write unit test",
        "implement a function", "implement a class", "implement a method",
        "implement an algorithm", "implement a data structure",
        "refactor this", "refactor the", "fix the bug", "debug this",
    )
    _CODE_LANG_MARKERS = (
        "python", "javascript", " js ", "typescript", " ts ",
        "node.js", "nodejs", "rust", "golang", " go ", "java ",
        "kotlin", "swift", "c++", "cpp", "c#", "csharp",
        "ruby", " php ", "sql", "bash", "shell", "powershell",
        "haskell", "scala", "elixir", "dart", "flutter",
        "react", "vue", "angular", "svelte", "django", "fastapi",
        "flask", "express", "spring", "laravel", "rails",
        "pandas", "numpy", "pytorch", "tensorflow",
    )
    _CODE_ACTION_MARKERS = (
        "функци", "класс", "метод", "алгоритм", "декоратор", "интерфейс",
        "скрипт", "программ", "модул", "библиотек", "компонент",
        "структур данных", "сложност", "асимптотик",
        "function", "class", "method", "algorithm", "decorator",
        "interface", "module", "library", "component", "data structure",
        "complexity", "big-o", "big o",
    )

    @classmethod
    def _looks_like_code(cls, text: str) -> bool:
        """Semantic check: does the text ask for code to be written/fixed/refactored?

        Word-group intersection safety net (NOT a primary gate — runs AFTER the
        LLM classifier). Fires when the text contains either a strong standalone
        marker ("напиши код", "pytest", "docstring", "write a function", …) OR a
        (language + action) combo like "Python" + "класс" / "Rust" + "function".
        A bare "что такое Python" has the lang marker but no action marker and
        falls through to the LLM classifier unchanged.
        """
        t = (text or "").lower()
        if not t:
            return False
        if any(m in t for m in cls._CODE_STRONG_MARKERS):
            return True
        has_lang = any(m in t for m in cls._CODE_LANG_MARKERS)
        has_action = any(m in t for m in cls._CODE_ACTION_MARKERS)
        return has_lang and has_action

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
        if has_topic and has_now:
            return True
        # Year heuristic: info-seeking verb ("найди", "расскажи про", "who won")
        # AND a 4-digit year — user explicitly anchors a question to a specific
        # year's event. Training-data cutoff is rarely a clean boundary: even
        # "мисс мира 2025" (last-year event, finaled in Dec) is past the cutoff
        # for many models. Safer to DDG on any explicit year + info-seek, and
        # let the synthesis LLM note "insufficient info" if the search is weak.
        # Catches "найди информацию о мисс мира 2025", "расскажи про выборы 2024",
        # "who won the championship 2023".
        if _YEAR_RE.search(t) and any(m in t for m in cls._WEB_SEEK_MARKERS):
            return True
        return False

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
        self,
        plan: list[SubTask],
        detected: DetectedInput,
        include_verifier: bool = False,
    ) -> str:
        subagents = [t.kind for t in plan]
        models = [t.model or "-" for t in plan]
        verifier_line = ""
        if include_verifier and self._should_fact_check(plan, detected):
            verifier_line = f"- **Verifiers:** `['fact_check']`\n"
        return (
            "<details>\n<summary>🎯 Routing decision</summary>\n\n"
            f"- **Lang:** `{detected.lang}`\n"
            f"- **Subagents:** `{subagents}`\n"
            f"{verifier_line}"
            f"- **Models:** `{models}`\n"
            f"- **Signals:** image={detected.has_image}, audio={detected.has_audio}, "
            f"doc={detected.has_document}, urls={len(detected.urls)}, "
            f"img_gen={detected.wants_image_gen}, web_search={detected.wants_web_search}\n"
            "\n</details>\n\n"
        )

    @staticmethod
    def _format_fact_check_for_prompt(report: dict) -> str:
        urls = report.get("urls", []) or []
        claims = report.get("claims", []) or []
        # If we have no claims to label, inject nothing — otherwise the
        # aggregator reads "use ✅/⚠️ labels" and sprinkles symbols randomly
        # through the answer with no report to back them up.
        if not claims:
            return ""
        ok = sum(1 for u in urls if u.get("status") in ("url_ok", "url_redirect"))
        broken = sum(1 for u in urls if u.get("status") in ("url_404", "url_unreachable"))
        lines = [
            "--- ATTRIBUTION REPORT ---",
            "Это проверка atribution: есть ли каждое утверждение в реально "
            "полученных snippets источников. Это НЕ оценка истинности — источник "
            "может и сам ошибаться; мы ловим только галлюцинации субагентов.",
            f"URLs checked: {ok} ok, {broken} broken, {len(urls)} total.",
            "Claims:",
        ]
        emoji = {"grounded": "✅", "partial": "⚠️", "ungrounded": "⚠️", "unknown": "⚠️"}
        for c in claims:
            tag = emoji.get(c.get("verdict", "unknown"), "⚠️")
            ev = f" — via {c['evidence_url']}" if c.get("evidence_url") else ""
            lines.append(f"  {tag} «{c.get('text','')}»{ev}")
        lines.append("---")
        lines.append(
            "Инструкции для ответа:\n"
            "1. СТАВЬ только символ ✅ или ⚠️ сразу после соответствующего "
            "утверждения, БЕЗ пробела перед ним и БЕЗ скобок.\n"
            "2. ЗАПРЕЩЕНО словами описывать характер проверки. Не пиши "
            "«подтверждено», «не подтверждено», «отмечено как недостоверное», "
            "«по данным источников», «согласно проверке» и т.п. — метки "
            "говорят сами за себя.\n"
            "3. ЗАПРЕЩЕНО утверждать, что какая-то информация ложная или "
            "фейковая — мы проверяем только attribution, а не истинность.\n"
            "4. Утверждения с ⚠️ можно смягчить формулировкой («сообщается», "
            "«встречается в одном источнике») — но БЕЗ отдельного комментария "
            "про проверку.\n"
            "5. Отвечай пользователю своим обычным языком; метки ✅/⚠️ — "
            "единственное видимое следствие этой проверки."
        )
        return "\n".join(lines)

    @staticmethod
    def _render_fact_check_details(report: dict) -> str:
        claims = report.get("claims", []) or []
        urls = report.get("urls", []) or []
        bad_urls = [u for u in urls if u.get("status") in ("url_404", "url_unreachable")]
        # Suppress the block entirely when there's nothing actionable to show
        # (no claims extracted and no broken URLs) — a silent pass is better
        # than a noisy empty block.
        if not claims and not bad_urls:
            return ""
        emoji_map = {"grounded": "✅", "partial": "⚠️", "ungrounded": "⚠️", "unknown": "⚠️"}
        label_ru = {
            "grounded": "есть в источнике",
            "partial": "частично в источнике",
            "ungrounded": "нет в источниках",
            "unknown": "не определено",
        }
        rows = []
        for c in claims:
            v = c.get("verdict", "unknown")
            em = emoji_map.get(v, "⚠️")
            lab = label_ru.get(v, v)
            line = f"- {em} **{lab}** — {c.get('text','')}"
            if c.get("evidence_url"):
                line += f"  \n  ↳ {c['evidence_url']}"
            rows.append(line)
        bad_lines = [f"- 🚫 {u.get('url')} ({u.get('status')})" for u in bad_urls]
        parts: list[str] = []
        if rows:
            parts.append("\n".join(rows))
        if bad_lines:
            parts.append("**Недоступные URL:**\n" + "\n".join(bad_lines))
        body = "\n\n".join(parts)
        # Heading emoji must match the worst-case content inside the block.
        # ✅ only when every claim is grounded AND every URL resolved; any
        # broken URL or non-grounded claim downgrades the heading to ⚠️, so
        # the user doesn't have to open the block to know something went off.
        all_grounded = bool(claims) and all(
            c.get("verdict") == "grounded" for c in claims
        )
        head_em = "✅" if all_grounded and not bad_urls else "⚠️"
        return (
            f"\n\n<details>\n<summary>{head_em} Проверка реальности источников</summary>\n\n"
            f"{body}\n\n</details>"
        )

    # ------------------------------------------------------------------
    # Phase 12 — URL validator (SSRF-hardened, async)
    # ------------------------------------------------------------------

    # Strip <script>/<style> bodies and all HTML tags, then collapse
    # whitespace — otherwise the verdict LLM receives HTML soup where
    # "+5 °C" lives inside <span class="temp">+5</span> markup and is
    # impossible to match against a plain-Russian claim. We keep a generous
    # 16 KB text window by default because weather/news pages front-load
    # ~8–12 KB of nav/markup before the real forecast table.
    _HTML_SCRIPT_RE = re.compile(
        r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>",
        re.DOTALL | re.IGNORECASE,
    )
    _HTML_TAG_RE = re.compile(r"<[^>]+>")
    _HTML_WS_RE = re.compile(r"\s+")

    @classmethod
    def _html_to_text(cls, body: str, limit: int = 16384) -> str:
        if not body:
            return ""
        body = cls._HTML_SCRIPT_RE.sub(" ", body)
        text = cls._HTML_TAG_RE.sub(" ", body)
        text = cls._HTML_WS_RE.sub(" ", text).strip()
        return text[:limit]

    @staticmethod
    def _salvage_json_array(content: str, array_key: str) -> dict:
        """Best-effort repair for truncated JSON from gpt-oss-20b in JSON mode.

        When `max_tokens` still gets exceeded, the model returns something like
        ``{"verdicts": [{"index":1,...}, {"index":2,"reason":"some long rea``
        (no closing quote, no `]`, no `}`). `json.loads` dies with
        "Unterminated string". We walk the string and keep all fully-closed
        objects inside the target array, then reassemble a valid JSON.

        Returns `{array_key: [...]}` with everything we could recover, or an
        empty dict if nothing is salvageable."""
        if not content or array_key not in content:
            return {}
        start = content.find("[", content.find(array_key))
        if start < 0:
            return {}
        depth = 0
        in_str = False
        esc = False
        last_good = -1
        for i in range(start, len(content)):
            ch = content[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    last_good = i
            elif ch == "]" and depth == 0:
                last_good = i
                break
        if last_good < 0:
            return {}
        head = content[start : last_good + 1]
        # If we stopped on `}` (not `]`), close the array manually.
        if not head.rstrip().endswith("]"):
            head = head + "]"
        try:
            arr = json.loads(head)
        except json.JSONDecodeError:
            return {}
        return {array_key: arr}

    _CLAIM_NORM_RE = re.compile(r"[^\w\s]+", re.UNICODE)

    @classmethod
    def _norm_claim(cls, text: str) -> str:
        """Normalize claim text for fallback-join with verdict entries that
        omit the `index` field: lowercase + strip punctuation + collapse
        whitespace + truncate to 120 chars. Makes the text-based lookup
        immune to trailing dots, capitalization, and inserted spaces."""
        if not text:
            return ""
        t = cls._CLAIM_NORM_RE.sub(" ", text.lower())
        t = cls._HTML_WS_RE.sub(" ", t).strip()
        return t[:120]

    @staticmethod
    def _dedupe_urls(
        results: list[CompactResult], max_urls: int
    ) -> tuple[list[str], dict[str, str]]:
        """Collect a deduplicated list of URLs from subagent results, plus a
        prefetched-bodies map. Respects the SSRF blocklist and the global
        max_urls cap.

        Attribution-correctness rule: if a subagent populated
        ``metadata["fetched_urls"]`` — the subset of citations whose page body
        the subagent actually read (not just pulled from a DDG snippet) — use
        ONLY those. A URL the agent didn't fetch is not a real source and must
        not feed the attribution check. Otherwise fall back to the previous
        behaviour (citations + URLs scraped from the summary text).

        When a result also carries ``metadata["fetched_bodies"]`` (a dict of
        url → page text already pulled by _fetch_url_text), that text is
        returned in the second element. _validate_urls uses it to skip the
        network round-trip entirely for these URLs — which is the only way to
        stop anti-bot sites (gismeteo, cloudflare-fronted pages) from
        responding OK to the first request and TCP-RST'ing the second."""
        seen: set[str] = set()
        ordered: list[str] = []
        prefetched: dict[str, str] = {}
        for r in results:
            meta = r.metadata or {}
            fetched = meta.get("fetched_urls")
            bodies = meta.get("fetched_bodies") or {}
            if fetched is not None:
                pool = list(fetched)
            else:
                pool = list(r.citations) + _URL_RE.findall(r.summary or "")
            for u in pool:
                u = u.rstrip(").,;:]»\"'")
                if u and u not in seen and not _SSRF_BLOCK_RE.match(u):
                    seen.add(u)
                    ordered.append(u)
                    body = bodies.get(u)
                    if body:
                        prefetched[u] = body
                    if len(ordered) >= max_urls:
                        return ordered, prefetched
        return ordered, prefetched

    async def _validate_urls(
        self,
        urls: list[str],
        prefetched: Optional[dict[str, str]] = None,
    ) -> list[UrlStatus]:
        """Parallel URL validator with SSRF guard and aggressive timeouts.

        If ``prefetched[u]`` already contains the page text (pulled earlier by
        _sa_web_search / _sa_deep_research via _fetch_url_text), we skip the
        network round-trip and return url_ok with that snippet. This is the
        fix for the gismeteo.ru / world-weather.ru class of bugs: some sites
        respond 200 to the first request from the search stage and then TCP-
        RST / 403 the validator's second request a second later, causing the
        same URL to end up both in the search summary AND in the
        "Недоступные URL" list.

        Uses GET (not HEAD) with a browser-like User-Agent for URLs we still
        need to fetch: many sites return 403/405/timeout on HEAD from a
        no-UA bot, and we'd falsely mark them url_unreachable. GET costs
        slightly more bandwidth but the 2 KB slice plus read cap bounds the
        hit. follow_redirects=True because we care about the final page
        content for attribution.

        Categories: url_ok / url_404 / url_auth_required / url_unreachable
        / url_blocked_ssrf. 401/403 are treated as url_auth_required
        (bot-gated but live), not broken links."""
        prefetched = prefetched or {}
        sem = asyncio.Semaphore(8)
        timeout = httpx.Timeout(
            self.valves.fact_check_url_timeout, connect=5.0
        )
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "*/*;q=0.8"
            ),
            "Accept-Language": "ru,en;q=0.9",
        }

        async def one(client: httpx.AsyncClient, u: str) -> UrlStatus:
            if _SSRF_BLOCK_RE.match(u):
                return UrlStatus(u, "url_blocked_ssrf")
            pre = prefetched.get(u)
            if pre:
                return UrlStatus(u, "url_ok", 200, u, pre[:16384])
            async with sem:
                try:
                    r = await client.get(u)
                    code = r.status_code
                    final = str(r.url) if str(r.url) != u else u
                    if code in (401, 403):
                        return UrlStatus(u, "url_auth_required", code, final)
                    if 400 <= code < 500:
                        return UrlStatus(u, "url_404", code, final)
                    if code >= 500:
                        return UrlStatus(u, "url_unreachable", code, final)
                    snippet = self._html_to_text(r.text or "", limit=16384)
                    return UrlStatus(u, "url_ok", code, final, snippet)
                except httpx.ConnectError:
                    return UrlStatus(u, "url_unreachable", None, error="connect")
                except httpx.TimeoutException:
                    return UrlStatus(u, "url_unreachable", None, error="timeout")
                except Exception as e:
                    return UrlStatus(
                        u, "url_unreachable", None, error=f"{type(e).__name__}"
                    )

        async with httpx.AsyncClient(
            timeout=timeout, follow_redirects=True, http2=False, headers=headers
        ) as client:
            return await asyncio.gather(*[one(client, u) for u in urls])

    # ------------------------------------------------------------------
    # Phase 12-4 — claim extractor (LLM)
    # ------------------------------------------------------------------

    async def _extract_claims(
        self,
        results: list[CompactResult],
        user_question: str,
    ) -> list[Claim]:
        """Extract up to `fact_check_max_claims` verifiable claims from the
        summaries of checkable subagents. Returns [] on empty input or LLM
        failure — never raises. Temperature is held at 0 for determinism."""
        checkable = [
            r for r in results
            if r.kind in _CHECKABLE_KINDS and not r.error and r.summary
        ]
        if not checkable:
            return []

        lines = [f"Вопрос пользователя: {user_question[:500]}"]
        for r in checkable:
            lines.append(f"--- {r.kind} ---\n{r.summary[:1500]}")
        user_msg = "\n\n".join(lines)

        content = ""
        try:
            resp = await self._call_litellm(
                model=self.valves.fact_check_claim_model,
                messages=[
                    {"role": "system", "content": _CLAIM_EXTRACT_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            content = (
                resp.get("choices", [{}])[0].get("message", {}).get("content")
                or "{}"
            )
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = self._salvage_json_array(content, "claims") or {}
                if not data:
                    raise
        except Exception as e:
            print(
                f"fact_check claim_extract FAILED: {type(e).__name__}: {e} | "
                f"content_head={content[:200]!r}"
            )
            return []

        raw = (data.get("claims") or [])[: self.valves.fact_check_max_claims]
        out: list[Claim] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if len(text) < 10:
                continue
            src = str(item.get("source_kind", "")).strip() or "web_search"
            out.append(Claim(text=text, source_kind=src))
        return out

    # ------------------------------------------------------------------
    # Phase 12-5 — verdict LLM
    # ------------------------------------------------------------------

    async def _verdict_claims(
        self,
        claims: list[Claim],
        url_statuses: list[UrlStatus],
        user_question: str,
    ) -> list[Claim]:
        """Assign grounded/partial/ungrounded/unknown attribution verdicts to
        each claim based on whether the snippets of validated URLs contain it.
        This is NOT a truth-check — we only detect subagent hallucinations
        (claims not traceable to any fetched source). Hallucinated "grounded"
        verdicts (evidence_url not present in the url_ok set) are downgraded
        to "ungrounded" and the URL is cleared. On LLM failure every claim
        returns with verdict="unknown" and reason="verdict_llm_failed" — the
        pipe must never crash here."""
        if not claims:
            return []

        evidence_lines: list[str] = []
        url_ok_set: set[str] = set()
        for us in url_statuses:
            if us.status in ("url_ok", "url_redirect") and us.snippet:
                ref = us.final_url or us.url
                # Feed ~4 KB of cleaned text per URL. Snippets arrive already
                # HTML-stripped from _validate_urls, so the LLM sees plain text
                # with forecast tables and news headlines intact. Keeps total
                # input bounded so the JSON output (verdicts with reason +
                # evidence_url per claim) fits into max_tokens=2500 without
                # tail truncation. Was 8000 → regularly blew the budget and
                # json.loads failed with "Unterminated string".
                evidence_lines.append(
                    f"URL: {ref}\nSnippet: {us.snippet[:4000]}"
                )
                url_ok_set.add(ref)
            elif us.status in ("url_404", "url_unreachable"):
                evidence_lines.append(
                    f"URL: {us.url} — НЕДОСТУПЕН ({us.status})"
                )
        evidence_text = (
            "\n\n".join(evidence_lines) if evidence_lines else "(нет доказательств)"
        )

        claims_text = "\n".join(
            f"[{i}] {c.text}" for i, c in enumerate(claims, start=1)
        )
        user_msg = (
            f"Вопрос пользователя: {user_question[:500]}\n\n"
            f"Утверждения для проверки (нумерация обязательна в ответе):\n{claims_text}\n\n"
            f"Доказательства:\n{evidence_text}"
        )

        async def _one_attempt(model: str) -> tuple[dict, str]:
            """Single verdict-LLM call. Returns (parsed_data, raw_content).
            Never raises — on any error returns ({}, content)."""
            content = ""
            try:
                resp = await self._call_litellm(
                    model=model,
                    messages=[
                        {"role": "system", "content": _VERDICT_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0,
                    max_tokens=2500,
                    response_format={"type": "json_object"},
                )
                content = (
                    resp.get("choices", [{}])[0].get("message", {}).get("content")
                    or "{}"
                )
                try:
                    return json.loads(content), content
                except json.JSONDecodeError:
                    salvaged = self._salvage_json_array(content, "verdicts") or {}
                    return salvaged, content
            except Exception as e:
                print(
                    f"fact_check verdict FAILED ({model}): {type(e).__name__}: {e} | "
                    f"content_head={content[:200]!r}"
                )
                return {}, content

        data, content = await _one_attempt(self.valves.fact_check_model)
        verdicts = (data or {}).get("verdicts") or []

        # Retry with the stronger fallback model if the primary returned an
        # empty object or empty verdicts list. gpt-oss-20b sometimes "gives
        # up" on dense homepage snippets (/world/, /rubric/mir) and emits
        # `{}` — kimi-k2 handles that kind of input better. Skipped when the
        # primary already errored out with json_failed (no sense burning a
        # second call if the first didn't even parse).
        fallback_model = self.valves.fact_check_fallback_model
        if not verdicts and fallback_model and fallback_model != self.valves.fact_check_model:
            print(
                f"fact_check verdict retry: primary={self.valves.fact_check_model} "
                f"returned empty, falling back to {fallback_model} | "
                f"user_msg_head={user_msg[:300]!r}"
            )
            # Hard cap on the retry — kimi-k2 on dense news snippets can hang
            # long enough to consume the whole fact_check_timeout budget and
            # turn the entire phase into `error=timeout`, losing even the
            # primary's result. Wrap the retry in its own deadline so we fail
            # fast and still render the trailing details block with whatever
            # verdicts we have (or with `unknown` for all of them).
            try:
                data2, content2 = await asyncio.wait_for(
                    _one_attempt(fallback_model), timeout=25.0
                )
            except asyncio.TimeoutError:
                print(
                    f"fact_check verdict retry TIMEOUT ({fallback_model}) after 25s"
                )
                data2, content2 = {}, ""
            verdicts2 = (data2 or {}).get("verdicts") or []
            if verdicts2:
                data, content, verdicts = data2, content2, verdicts2

        # If BOTH primary and fallback failed to parse/return anything, surface
        # the whole thing as verdict_llm_failed.
        if data is None:
            data = {}
        if not verdicts and not data:
            print(
                f"fact_check verdict BOTH FAILED | content_head={content[:200]!r}"
            )
            return [
                Claim(
                    text=c.text,
                    source_kind=c.source_kind,
                    verdict="unknown",
                    reason="verdict_llm_failed",
                )
                for c in claims
            ]
        # Primary join is by `index` (as required by _VERDICT_PROMPT), but
        # gpt-oss-20b sometimes ignores that instruction and returns the older
        # shape `{"claim": "...", "verdict": "..."}`. Build a second by-text
        # index as a fallback so we don't silently default everything to
        # "unknown" on prompt drift.
        by_index: dict[int, dict] = {}
        by_text: dict[str, dict] = {}
        unmatched: list[dict] = []
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            placed = False
            raw_idx = v.get("index")
            if raw_idx is not None:
                try:
                    idx = int(raw_idx)
                except (TypeError, ValueError):
                    idx = 0
                if 1 <= idx <= len(claims):
                    by_index[idx] = v
                    placed = True
            txt_key = self._norm_claim(str(v.get("claim", "")))
            if txt_key:
                by_text.setdefault(txt_key, v)
                placed = True
            if not placed:
                unmatched.append(v)

        # Positional fallback: if the LLM returned verdicts in the same order
        # but without index/claim fields, drain unmatched into remaining slots.
        leftovers = list(unmatched)

        out: list[Claim] = []
        matched_count = 0
        for i, c in enumerate(claims, start=1):
            v = by_index.get(i)
            if v is None:
                v = by_text.get(self._norm_claim(c.text))
            if v is None and leftovers:
                v = leftovers.pop(0)
            if v is None:
                v = {}
            else:
                matched_count += 1
            verdict = str(v.get("verdict", "unknown")).lower().strip()
            if verdict not in ("grounded", "partial", "ungrounded"):
                verdict = "unknown"
            ev_url = str(v.get("evidence_url", "")).strip() or None
            # Critical guard: grounded without a real URL from the ok-set is
            # a hallucination by the verdict LLM itself — downgrade to
            # ungrounded and clear the URL.
            if verdict == "grounded" and (not ev_url or ev_url not in url_ok_set):
                verdict = "ungrounded"
                ev_url = None
            out.append(
                Claim(
                    text=c.text,
                    source_kind=c.source_kind,
                    verdict=verdict,
                    evidence_url=ev_url,
                    reason=str(v.get("reason", "")).strip()[:300],
                )
            )
        # Visibility: distinguish the two silent-failure modes so we can see
        # *why* every claim might come back as "unknown".
        #   - verdicts list empty → LLM returned {} or {"verdicts":[]} (did
        #     not even try to label claims; usually a prompt/model issue).
        #   - verdicts present but nothing joined → shape drift (wrong
        #     indices, missing claim text, wrong field names).
        if not verdicts:
            print(
                "fact_check verdict EMPTY VERDICTS: "
                f"claims={len(claims)} urls_ok={len(url_ok_set)} "
                f"raw_data={str(data)[:400]}"
            )
        elif matched_count == 0:
            print(
                "fact_check verdict JOIN EMPTY: "
                f"claims={len(claims)} verdicts={len(verdicts)} "
                f"raw={str(verdicts)[:400]}"
            )
        return out

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
        fc_result = next((r for r in results if r.kind == "fact_check"), None)
        checkable_results = [r for r in results if r.kind != "fact_check"]
        # Build scratchpad from compact summaries only
        lines: list[str] = []
        for r in checkable_results:
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
        # Inject current date so the aggregator can answer time-aware questions
        # ("какой сегодня день", "what's today's date", "сколько до нового года")
        # without relying on the LLM's stale training cutoff.
        today_iso = datetime.now(timezone.utc).date().isoformat()
        date_line = f"Current date: {today_iso}\n"
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
            f"{date_line}"
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
            "ОТДЕЛЬНО про [sa_memory_recall]: если этот субагент вернул список эпизодов, "
            "сгруппированных по чатам (секции вида '### Чат «...» [даты]'), "
            "сохрани эту структуру В ТОЧНОСТИ: каждый чат — отдельный раздел с заголовком "
            "и датой, не сливай эпизоды разных чатов в один абзац, не переписывай даты, "
            "не обобщай в единое резюме. Можно слегка перефразировать отдельные пункты, "
            "но границы чатов и даты должны быть видны пользователю. "
            f"{lang_instr}"
            f"{memory_block}"
            f"\n\n--- SUBAGENT RESULTS ---\n{scratchpad}"
        )

        # NOTE: we deliberately do NOT inject the attribution report into the
        # aggregator's system prompt. When we did, the LLM sprinkled ✅/⚠️
        # through the answer without any real mapping to verdicts — it just
        # pattern-matched "use these labels" and decorated every bullet with
        # ✅ even when the details block said everything was ungrounded. The
        # per-claim attribution now lives exclusively in the trailing
        # <details>Проверка реальности источников</details> block emitted by
        # pipe() via _render_fact_check_details. The aggregator writes the
        # answer unaware of fact_check; the user gets a clean body plus an
        # independent audit section with no inline contradictions.

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
        today_iso = datetime.now(timezone.utc).date().isoformat()
        return await self._text_subagent(
            model=task.model or "mws/gpt-alpha",
            system=(
                f"Current date: {today_iso}\n"
                "You are a helpful, concise assistant. Answer in English in markdown."
            ),
            task=task,
            temperature=0.7,
        )

    async def _sa_ru_chat(self, task: SubTask) -> CompactResult:
        today_iso = datetime.now(timezone.utc).date().isoformat()
        return await self._text_subagent(
            model=task.model or "mws/qwen3-235b",
            system=(
                f"Current date: {today_iso}\n"
                "Ты — дружелюбный и лаконичный ассистент. Отвечай на русском в markdown."
            ),
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
                return await self._fetch_url_text(u)
            except Exception:
                return ""

        bodies = await asyncio.gather(*[_safe_fetch(h["url"]) for h in hits])
        fetched_urls = [h["url"] for h, body in zip(hits, bodies) if body]
        fetched_bodies = {
            h["url"]: body for h, body in zip(hits, bodies) if body
        }
        snippets_block = "\n\n".join(
            f"[{i+1}] {h['title']} — {h['url']}\n{(body[:2000] if body else h['snippet'])}"
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
            metadata={
                "fetched_urls": fetched_urls,
                "fetched_bodies": fetched_bodies,
            },
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
        exclude_chat_id = task.metadata.get("exclude_chat_id")
        # Fetch more episodes than we'll show so that after grouping by chat
        # we still have something meaningful (5 top episodes may all come from
        # the same chat).
        payload: dict = {
            "user_id": user_id,
            "query": task.input_text,
            "limit": 10,
        }
        if exclude_chat_id:
            payload["exclude_chat_id"] = exclude_chat_id
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

        # Group episodes by chat_id so the aggregator sees per-chat sections
        # instead of a flat list it's tempted to melt into one paragraph.
        groups: dict[str, list[dict]] = {}
        order: list[str] = []
        for ep in episodes:
            cid = ep.get("chat_id") or "unknown"
            if cid not in groups:
                groups[cid] = []
                order.append(cid)
            groups[cid].append(ep)

        # Resolve chat titles from openwebui's postgres (best effort, short
        # timeout). Without this the aggregator only sees opaque uuids.
        titles = await self._fetch_chat_titles(order)

        sections: list[str] = []
        for cid in order:
            eps = groups[cid]
            # Date range of this chat's episodes.
            dates = sorted(
                (ep.get("turn_end_at") or "")[:10] for ep in eps if ep.get("turn_end_at")
            )
            date_label = (
                f"{dates[0]} … {dates[-1]}" if dates and dates[0] != dates[-1]
                else (dates[0] if dates else "")
            )
            title = titles.get(cid) or "(без названия)"
            header = f"### Чат «{title}» [{date_label}] (id={cid[:8]})"
            bullets = [
                f"- [{(ep.get('turn_end_at') or '')[:10]}] {(ep.get('summary') or '').strip()}"
                for ep in eps
            ]
            sections.append(header + "\n" + "\n".join(bullets))

        body = (
            "Найденные эпизоды из прошлых диалогов (сгруппировано по чатам):\n\n"
            + "\n\n".join(sections)
        )
        return CompactResult(
            kind="memory_recall",
            summary=self._truncate_tokens(body, 700),
            citations=[cid for cid in order if cid and cid != "unknown"],
        )

    async def _fetch_chat_titles(self, chat_ids: list[str]) -> dict[str, str]:
        """Best-effort lookup of chat titles from openwebui's postgres.

        Uses the OWUI admin API so we don't hardcode DB credentials into the
        pipe. Returns {} on any failure — memory_recall still works without
        titles, it just shows chat uuids.
        """
        token = os.getenv("OWUI_ADMIN_TOKEN", "")
        if not token or not chat_ids:
            return {}
        titles: dict[str, str] = {}
        try:
            async with httpx.AsyncClient(timeout=5) as cli:
                # OpenWebUI has no bulk endpoint; fetch per-id. Cap at 10.
                for cid in chat_ids[:10]:
                    try:
                        r = await cli.get(
                            f"http://localhost:8080/api/v1/chats/{cid}",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        if r.status_code == 200:
                            data = r.json() or {}
                            titles[cid] = (data.get("title") or "").strip()
                    except Exception:
                        continue
        except Exception as e:
            if self.valves.debug:
                print(f"[mws-auto] chat title lookup failed: {e}")
        return titles

    # ------------------------------------------------------------------
    # Stubs (v2)
    # ------------------------------------------------------------------

    async def _sa_deep_research(self, task: SubTask) -> CompactResult:
        """Multi-query research: split the question into sub-queries, DDG each
        in parallel, fetch top pages, synthesize a structured answer with
        citations. Heavier than _sa_web_search (single query) — use when the
        user explicitly asks for research/investigation, or the classifier
        picks intent="deep_research".
        """
        query = task.input_text
        if not query:
            return CompactResult(kind="deep_research", error="empty query")

        lang = task.metadata.get("lang", "ru")
        today = datetime.now(timezone.utc).date().isoformat()

        # Step 1 — split the question into 3-5 focused sub-queries.
        # gpt-oss-20b is cheap and fast; temperature low so the splits are
        # deterministic and topical rather than creative.
        split_system = (
            f"Today is {today}. You are a research planner. Split the user's "
            "question into 3-5 focused web search queries that together fully "
            "cover the topic. Each query must be a standalone search-engine "
            "string (not a full sentence), on its own line, no numbering, no "
            "bullets, no commentary. If the topic mentions a specific year, "
            "include that year in every query. Prefer short queries (5-10 words)."
        )
        sub_queries: list[str] = []
        try:
            split_resp = await self._call_litellm(
                model="mws/gpt-oss-20b",
                messages=[
                    {"role": "system", "content": split_system},
                    {"role": "user", "content": query},
                ],
                temperature=0.2,
                max_tokens=250,
            )
            split_text = (
                split_resp.get("choices", [{}])[0]
                .get("message", {})
                .get("content")
                or ""
            )
            for line in split_text.splitlines():
                q = line.strip(" \t-•*0123456789.)").strip()
                if q and len(q) >= 3:
                    sub_queries.append(q)
            sub_queries = sub_queries[:5]
        except Exception as e:
            if self.valves.debug:
                print(f"[mws-auto] sa_deep_research split failed: {e}")

        # Fallback: if split failed, use the original query alone.
        if not sub_queries:
            sub_queries = [query]

        # Step 2 — run DDG in parallel across all sub-queries.
        async def _safe_search(q: str) -> list[dict]:
            try:
                return await self._ddg_search(q, n=3)
            except Exception:
                return []

        search_results = await asyncio.gather(
            *[_safe_search(q) for q in sub_queries]
        )

        # Dedupe by URL, preserve order (first-seen wins).
        seen: set[str] = set()
        all_hits: list[dict] = []
        for hits in search_results:
            for h in hits:
                u = h.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    all_hits.append(h)
        # Cap total sources: 8 is enough for synthesis, keeps latency sane.
        all_hits = all_hits[:8]

        if not all_hits:
            return CompactResult(
                kind="deep_research",
                error="no search results for any sub-query",
            )

        # Step 3 — fetch page bodies in parallel (best-effort; snippet fallback).
        async def _safe_fetch(u: str) -> str:
            try:
                return await self._fetch_url_text(u)
            except Exception:
                return ""

        bodies = await asyncio.gather(
            *[_safe_fetch(h["url"]) for h in all_hits]
        )
        fetched_urls = [h["url"] for h, body in zip(all_hits, bodies) if body]
        fetched_bodies = {
            h["url"]: body for h, body in zip(all_hits, bodies) if body
        }
        context_block = "\n\n".join(
            f"[{i+1}] {h['title']} — {h['url']}\n{(body[:2500] if body else h.get('snippet',''))}"
            for i, (h, body) in enumerate(zip(all_hits, bodies))
        )
        sub_queries_block = "\n".join(f"- {q}" for q in sub_queries)

        # Step 4 — synthesize a structured answer with citations.
        lang_hint = (
            "Отвечай на русском языке." if lang == "ru"
            else "Answer in English."
        )
        synth_system = (
            f"Today is {today}. Ты — исследовательский агент. Тебе даны "
            "исходный вопрос, сгенерированные подзапросы и фрагменты из "
            "нескольких источников. Сделай структурированный ответ:\n"
            "1) Краткое резюме (2-3 предложения) с ключевым выводом.\n"
            "2) Основные факты с цитированием источников в формате [1], [2] "
            "и т.д. — цитируй только те номера, что реально приведены ниже.\n"
            "3) Если источники противоречат друг другу — укажи это явно.\n"
            "4) Если информации недостаточно для уверенного ответа — скажи "
            "об этом прямо, не выдумывай факты.\n"
            "Не придумывай URL'ы и не добавляй несуществующие источники. "
            + lang_hint
        )
        try:
            resp = await self._call_litellm(
                model=task.model or "mws/kimi-k2",
                messages=[
                    {"role": "system", "content": synth_system},
                    {
                        "role": "user",
                        "content": (
                            f"Исходный вопрос: {query}\n\n"
                            f"Подзапросы ({len(sub_queries)}):\n{sub_queries_block}\n\n"
                            f"Источники ({len(all_hits)}):\n{context_block}"
                        ),
                    },
                ],
                temperature=0.35,
                max_tokens=900,
            )
        except Exception as e:
            return CompactResult(
                kind="deep_research",
                error=f"synthesis failed: {e}",
                citations=[h["url"] for h in all_hits],
            )
        text = (
            resp.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
        if not text:
            return CompactResult(
                kind="deep_research",
                error="empty synthesis",
                citations=[h["url"] for h in all_hits],
            )
        return CompactResult(
            kind="deep_research",
            summary=self._truncate_tokens(text, 700),
            citations=[h["url"] for h in all_hits],
            metadata={
                "sub_queries": sub_queries,
                "sources": len(all_hits),
                "fetched_urls": fetched_urls,
                "fetched_bodies": fetched_bodies,
            },
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

    # ------------------------------------------------------------------
    # Phase 12 — FactChecker subagent (skeleton)
    # ------------------------------------------------------------------
    # These are stubs so the pipe loads; real implementations land in
    # phase-12-4 .. phase-12-8. Wiring from pipe() / _stream_aggregate is
    # intentionally NOT added here — that happens in phase-12-7 and 12-8.

    def _should_fact_check(
        self, plan: list[SubTask], detected: DetectedInput
    ) -> bool:
        if not self.valves.fact_check_enabled:
            return False
        if _FACT_CHECK_TRIGGER_RE.search(detected.last_user_text or ""):
            return True
        kinds = {t.kind for t in plan}
        return bool(kinds & _CHECKABLE_KINDS)

    async def _sa_fact_check(
        self,
        results: list[CompactResult],
        detected: DetectedInput,
        user_question: str,
    ) -> CompactResult:
        """Phase-1.5 orchestrator: dedupe URLs → validate + extract claims in
        parallel → verdict claims. Returns a CompactResult with the full
        FactCheckReport under metadata["report"]."""
        checkable = [
            r for r in results
            if r.kind in _CHECKABLE_KINDS and not r.error
        ]
        if not checkable:
            return CompactResult(kind="fact_check", summary="nothing to check")

        async def _do() -> FactCheckReport:
            urls, prefetched = self._dedupe_urls(
                checkable, self.valves.fact_check_max_urls
            )
            url_task = (
                asyncio.create_task(self._validate_urls(urls, prefetched))
                if urls
                else None
            )
            claim_task = asyncio.create_task(self._extract_claims(checkable, user_question))
            url_statuses = await url_task if url_task else []
            claims = await claim_task
            claims_with_verdict = await self._verdict_claims(claims, url_statuses, user_question)
            return FactCheckReport(
                urls=url_statuses,
                claims=claims_with_verdict,
                total_checked_kinds=sorted({r.kind for r in checkable}),
            )

        try:
            report = await asyncio.wait_for(_do(), timeout=self.valves.fact_check_timeout)
        except asyncio.TimeoutError:
            return CompactResult(
                kind="fact_check",
                summary="fact-check timed out",
                error="timeout",
            )
        except Exception as e:
            print(f"fact_check FAILED: {type(e).__name__}: {e}")
            return CompactResult(
                kind="fact_check",
                summary=f"fact-check failed: {type(e).__name__}",
                error=str(e)[:200],
            )

        total = len(report.claims)
        grounded = sum(1 for c in report.claims if c.verdict == "grounded")
        ungrounded = sum(1 for c in report.claims if c.verdict == "ungrounded")
        url_ok = sum(1 for u in report.urls if u.status in ("url_ok", "url_redirect"))
        summary = (
            f"attribution: {grounded}/{total} grounded, {ungrounded} ungrounded, "
            f"URLs ok={url_ok}/{len(report.urls)}"
        )
        return CompactResult(
            kind="fact_check",
            summary=summary,
            metadata={"report": asdict(report)},
        )
