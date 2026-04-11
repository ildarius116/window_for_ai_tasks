import hashlib
import logging

import httpx

from app.config import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    LITELLM_API_KEY,
    LITELLM_URL,
)

logger = logging.getLogger(__name__)
logger.info("Embedding model: %s, dim=%d", EMBEDDING_MODEL, EMBEDDING_DIMENSIONS)

# Simple in-memory cache to avoid re-embedding identical texts
_cache: dict[str, list[float]] = {}


class EmbeddingError(RuntimeError):
    pass


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector via LiteLLM `/v1/embeddings`.

    Uses the configured `EMBEDDING_MODEL` (default `mws/bge-m3`, routed
    through LiteLLM to MWS GPT API). On any upstream failure raises
    `EmbeddingError` — we never return hash-based pseudo-vectors, because
    cosine search over garbage vectors silently corrupts recall.
    """
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LITELLM_URL}/v1/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text, "encoding_format": "float"},
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
    except Exception as e:
        raise EmbeddingError(
            f"LiteLLM embeddings unavailable: {e}"
        ) from e

    if resp.status_code != 200:
        raise EmbeddingError(
            f"LiteLLM /v1/embeddings returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    vec = data["data"][0]["embedding"]
    if len(vec) != EMBEDDING_DIMENSIONS:
        logger.warning(
            "Embedding dim mismatch: got %d, expected %d (model=%s). "
            "Check EMBEDDING_DIMENSIONS config.",
            len(vec),
            EMBEDDING_DIMENSIONS,
            EMBEDDING_MODEL,
        )
    _cache[cache_key] = vec
    return vec
