"""
MWS pptx-service — generates real .pptx files from a source document + user instruction.

Flow:
    POST /build (multipart)
      file           : optional uploaded PDF/DOCX/TXT
      source_text    : optional raw text (alternative to file)
      user_instruction: optional extra direction ("сделай на 5 слайдов", etc.)
    → parse source → LLM (LiteLLM JSON mode) → PresentationSchema
    → python-pptx render → streaming .pptx bytes
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from io import BytesIO
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from builder import build_pptx
from image_gen import generate_image, generate_many
from parsing import MAX_CHARS, UnsupportedFormat, extract_text
from schema_llm import SchemaGenerationError, generate_schema

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("pptx-service")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

app = FastAPI(title="MWS pptx-service", version="1.0.0")


@app.middleware("http")
async def _timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    dur_ms = (time.perf_counter() - started) * 1000
    log.info("%s %s -> %s in %.1fms", request.method, request.url.path, response.status_code, dur_ms)
    return response


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/build")
async def build(
    file: Optional[UploadFile] = File(None),
    user_instruction: str = Form(""),
    source_text: Optional[str] = Form(None),
):
    # --- Collect source text ---
    if file is not None and file.filename:
        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"file too large: {len(raw)} bytes > {MAX_UPLOAD_BYTES}",
            )
        if not raw:
            raise HTTPException(status_code=400, detail="empty file upload")
        try:
            src = extract_text(file.filename, raw)
        except UnsupportedFormat as e:
            raise HTTPException(status_code=415, detail=str(e)) from e
        except Exception as e:
            log.exception("parse failed: %s", e)
            raise HTTPException(status_code=400, detail=f"parse failed: {e}") from e
        if not src.strip():
            raise HTTPException(status_code=400, detail="document produced empty text")
    elif source_text and source_text.strip():
        src = source_text.strip()[:MAX_CHARS]
    elif user_instruction and user_instruction.strip():
        src = ""  # model will generate from instruction alone
    else:
        raise HTTPException(status_code=400, detail="empty input")

    # --- LLM schema ---
    try:
        schema = await generate_schema(src, user_instruction)
    except SchemaGenerationError as e:
        log.warning("schema generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"schema generation failed: {e}") from e

    if not schema.slides:
        raise HTTPException(status_code=502, detail="LLM returned zero slides")

    # --- Generate images in parallel (cover + per-slide) with a hard
    # overall deadline so upstream flakiness can't blow the whole /build
    # past the pipe's httpx timeout. If we blow the deadline, render a
    # text-only deck — the user still gets something quickly.
    cover_bytes: Optional[bytes] = None
    slide_bytes: list[Optional[bytes]] = [None] * len(schema.slides)
    IMAGE_STAGE_DEADLINE = int(os.getenv("IMAGE_STAGE_DEADLINE", "45"))
    try:
        cover_task = (
            generate_image(schema.cover_image_prompt)
            if schema.cover_image_prompt
            else asyncio.sleep(0, result=None)
        )
        slide_prompts: list[Optional[str]] = [s.image_prompt for s in schema.slides]
        slide_task = generate_many(slide_prompts)
        cover_bytes, slide_bytes = await asyncio.wait_for(
            asyncio.gather(cover_task, slide_task),
            timeout=IMAGE_STAGE_DEADLINE,
        )
    except asyncio.TimeoutError:
        log.warning(
            "image generation stage hit %ss deadline — rendering text-only",
            IMAGE_STAGE_DEADLINE,
        )
        cover_bytes = None
        slide_bytes = [None] * len(schema.slides)
    except Exception as e:
        log.warning("image generation stage failed (rendering without images): %s", e)
        cover_bytes = None
        slide_bytes = [None] * len(schema.slides)
    generated_count = (1 if cover_bytes else 0) + sum(1 for b in slide_bytes if b)

    # --- Render ---
    try:
        data = build_pptx(schema, cover_image=cover_bytes, slide_images=slide_bytes)
    except Exception as e:
        log.exception("build_pptx failed: %s", e)
        raise HTTPException(status_code=500, detail=f"pptx render failed: {e}") from e

    safe_title = _slug(schema.title) or "presentation"
    # X-Title-B64 carries the UTF-8 title as base64 because HTTP headers are latin-1 only.
    import base64 as _b64
    title_b64 = _b64.b64encode(schema.title[:200].encode("utf-8")).decode("ascii")
    return StreamingResponse(
        BytesIO(data),
        media_type=PPTX_MIME,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_title}.pptx"',
            "X-Slide-Count": str(len(schema.slides) + 1),
            "X-Title-B64": title_b64,
        },
    )


@app.exception_handler(HTTPException)
async def _http_exc(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _slug(text: str) -> str:
    text = (text or "").strip().lower()
    out: list[str] = []
    for ch in text:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum() or ch in " _-"):
            out.append(ch)
        else:
            out.append(" ")
    slug = re.sub(r"\W+", "_", "".join(out)).strip("_")
    return slug[:60] or "presentation"
