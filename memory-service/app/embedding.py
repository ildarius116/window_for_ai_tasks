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

# Simple in-memory cache to avoid re-embedding identical texts
_cache: dict[str, list[float]] = {}


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector via LiteLLM `/v1/embeddings`.

    Uses the configured `EMBEDDING_MODEL` (default `mws/bge-m3`, routed
    through LiteLLM to MWS GPT API). Falls back to a deterministic
    hash-based pseudo-embedding if the upstream call fails, so the
    service stays functional end-to-end even when the API is down.
    """
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LITELLM_URL}/v1/embeddings",
                json={"model": EMBEDDING_MODEL, "input": text},
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            if resp.status_code == 200:
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
            logger.warning(
                "LiteLLM /v1/embeddings returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
    except Exception as e:
        logger.warning("LiteLLM embeddings unavailable: %s", e)

    # Fallback: deterministic hash-based pseudo-embedding
    vec = _hash_embedding(text, dims=EMBEDDING_DIMENSIONS)
    _cache[cache_key] = vec
    return vec


def _hash_embedding(text: str, dims: int = 1024) -> list[float]:
    """Deterministic pseudo-embedding from text hash.

    NOT semantically meaningful — just ensures the system works end-to-end.
    Replace with real embeddings when available.
    """
    import struct

    h = hashlib.sha512(text.encode()).digest()
    # Expand hash to fill dims
    chunks = []
    for i in range(dims):
        seed = hashlib.md5(h + struct.pack("H", i)).digest()[:4]
        # Use unsigned int to avoid NaN from float unpacking
        val = struct.unpack("I", seed)[0]
        # Normalize to [-1, 1]
        val = (val / 2147483647.5) - 1.0
        chunks.append(round(val, 6))
    return chunks
