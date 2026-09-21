"""Async Elasticsearch client factory + index bootstrap.

Lives at the infra layer (mirror of `infra/qdrant/`) so multiple domains
can share a single cluster client. YCS owns the only consumer today —
metadata + transcriptions indexes.

Deprecated provenance: `app.py:L105-113` (client init) +
`helpers.py:L1862-2000` (`create_youtube_indexes`)."""
from __future__ import annotations

import logging
from typing import Optional

from elasticsearch import AsyncElasticsearch

from . import domain, params


logger = logging.getLogger(__name__)


_client: Optional[AsyncElasticsearch] = None


def get_es() -> AsyncElasticsearch:
    """Process-bound singleton — one async client / connection pool reused."""
    global _client
    if _client is None:
        _client = AsyncElasticsearch(
            hosts = [params.ES_HOST],
            basic_auth = domain.basic_auth(params.ES_USERNAME, params.ES_PASSWORD),
            verify_certs = params.ES_VERIFY_CERTS,
            request_timeout = params.TIMEOUT_S,
        )
        logger.info(f"[elasticsearch] client init {params.ES_HOST}")
    return _client


async def close_es() -> None:
    """Lifespan shutdown. Idempotent."""
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception as e:
            logger.warning(f"[elasticsearch] close failed: {e}")
        _client = None


async def ensure_indexes() -> dict:
    """Idempotently create the two deprecated YCS indexes.

    Returns a per-index status dict — same shape as deprecated
    `create_youtube_indexes` so a future debug endpoint could surface
    it. Failures don't raise; they're best-effort with structured
    error reporting so the rest of the lifespan keeps going."""
    es = get_es()
    results: dict[str, dict] = {}
    for index, mapping in domain.index_pairs():
        try:
            exists = await es.indices.exists(index = index)
            if not exists:
                await es.indices.create(
                    index = index,
                    mappings = mapping["mappings"],
                    settings = mapping["settings"],
                )
                results[index] = {"created": True}
                logger.info(f"[elasticsearch] created index {index!r}")
            else:
                results[index] = {"created": False, "reason": "exists"}
        except Exception as e:
            results[index] = {
                "created": False,
                "error": f"{type(e).__name__}: {e}",
            }
            logger.warning(
                f"[elasticsearch] ensure_index {index!r} failed: {e}"
            )
    return results
