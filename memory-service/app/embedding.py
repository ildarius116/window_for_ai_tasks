import hashlib
import logging

import httpx

from app.config import LITELLM_API_KEY, LITELLM_URL

logger = logging.getLogger(__name__)

# Simple in-memory cache to avoid re-embedding identical texts
_cache: dict[str, list[float]] = {}


async def get_embedding(text: str) -> list[float]:
    """Get embedding vector via LiteLLM chat completions (simulated).

    Since free OpenRouter models don't expose /embeddings, we use a
    lightweight hashing approach for the MVP and upgrade to real
    embeddings when an embedding-capable model is available.
    """
    cache_key = hashlib.md5(text.encode()).hexdigest()
    if cache_key in _cache:
        return _cache[cache_key]

    # Try LiteLLM /embeddings endpoint first
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{LITELLM_URL}/v1/embeddings",
                json={"model": "text-embedding-3-small", "input": text},
                headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                vec = data["data"][0]["embedding"]
                _cache[cache_key] = vec
                return vec
    except Exception as e:
        logger.debug("LiteLLM embeddings unavailable: %s", e)

    # Fallback: deterministic hash-based pseudo-embedding (768 dims)
    vec = _hash_embedding(text, dims=768)
    _cache[cache_key] = vec
    return vec


def _hash_embedding(text: str, dims: int = 768) -> list[float]:
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
