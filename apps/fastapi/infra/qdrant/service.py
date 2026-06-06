"""Async Qdrant client factory + lifecycle.

Lives at the infra layer (not under any one domain) because multiple
domains will share a single cluster client — YCS holds video chunks
under one collection; future DD code may add a separate collection."""
from __future__ import annotations

import logging
from typing import Optional

from qdrant_client import AsyncQdrantClient

from .params import (
    QDRANT_API_KEY,
    QDRANT_HOST,
    QDRANT_HTTPS,
    QDRANT_PORT,
    TIMEOUT_S,
)


logger = logging.getLogger(__name__)

_client: Optional[AsyncQdrantClient] = None


def get_qdrant() -> AsyncQdrantClient:
    """Process-bound singleton — one HTTP/2 connection pool reused."""
    global _client
    if _client is None:
        _client = AsyncQdrantClient(
            host = QDRANT_HOST,
            port = QDRANT_PORT,
            api_key = QDRANT_API_KEY,
            https = QDRANT_HTTPS,
            timeout = TIMEOUT_S,
        )
        logger.info(
            f"[qdrant] client init {QDRANT_HOST}:{QDRANT_PORT} https={QDRANT_HTTPS}"
        )
    return _client


async def close_qdrant() -> None:
    """Lifespan close. Idempotent."""
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception as e:
            logger.warning(f"[qdrant] close failed: {e}")
        _client = None
