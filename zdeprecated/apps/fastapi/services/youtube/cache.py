"""
Redis Cache for RAG Responses

CONCEPT: RAG queries are expensive — each one triggers retrieval, grading
(N LLM calls), generation, and hallucination checking. Caching avoids
repeating this work for identical or similar questions.

Cache strategy:
- Key: SHA-256 hash of the question text
- Value: JSON with answer, sources, citations, timestamp
- TTL: configurable (default 1 hour) — transcripts don't change often
- Invalidation: manual via /ingest (new data = clear relevant cache)

Redis JSON (via RedisJSON module in Redis Stack) stores structured data
natively, so we don't need to serialize/deserialize manually.
"""
import hashlib
import json
import time
import redis.asyncio as redis_aio


CACHE_PREFIX = "coelhonexus:rag:cache:"
DEFAULT_TTL = 3600  # 1 hour


def _cache_key(question: str, mode: str | None = None) -> str:
    """Generate a deterministic cache key from the question and optional mode."""
    raw = question.strip().lower()
    if mode:
        raw += f"|mode={mode}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{CACHE_PREFIX}{h}"


async def get_cached_response(
    redis: redis_aio.Redis,
    question: str,
    mode: str | None = None,
) -> dict | None:
    """
    Check if a cached response exists for this question.
    Returns the cached dict or None if not found / expired.
    """
    key = _cache_key(question, mode)
    try:
        data = await redis.get(key)
        if data:
            return json.loads(data)
    except Exception:
        pass
    return None


async def cache_response(
    redis: redis_aio.Redis,
    question: str,
    response: dict,
    ttl: int = DEFAULT_TTL,
    mode: str | None = None,
):
    """
    Cache a RAG response with TTL.

    CONCEPT: We store the complete response (answer, sources, citations)
    so that cache hits skip the ENTIRE pipeline — no retrieval, no grading,
    no generation. This is a huge cost and latency saving for repeated queries.
    """
    key = _cache_key(question, mode)
    payload = {
        **response,
        "_cached_at": time.time(),
    }
    try:
        await redis.set(key, json.dumps(payload), ex = ttl)
    except Exception:
        pass  # Cache failures are non-critical


async def invalidate_cache(
    redis: redis_aio.Redis,
    question: str | None = None,
):
    """
    Invalidate cache entries.
    If question is provided, invalidates that specific entry.
    If None, invalidates ALL RAG cache entries.
    """
    try:
        if question:
            key = _cache_key(question)
            await redis.delete(key)
        else:
            # Scan and delete all cache keys
            async for key in redis.scan_iter(match = f"{CACHE_PREFIX}*"):
                await redis.delete(key)
    except Exception:
        pass
