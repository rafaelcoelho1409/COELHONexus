"""Async Elasticsearch client factory + index bootstrap.

Lives at the infra layer (mirror of `infra/qdrant/`) so multiple domains
can share a single cluster client. YCS uses metadata and transcriptions
indexes; BD Ingestion uses its processed-page index.

The only module that imports the `elasticsearch` library or reads
`ELASTICSEARCH_*` env vars: the FastAPI process uses the `get_es()`
singleton, Celery tasks open their own client with `new_es()`, and the
`AsyncElasticsearch` type is reached dotted through here.

Index names are defined in `keys.py`; their mappings are in `schemas.py`."""
from __future__ import annotations
import infra
from . import domain, params

import logging
from typing import Optional

from elasticsearch import AsyncElasticsearch


logger = logging.getLogger(__name__)


_client: Optional[AsyncElasticsearch] = None


def new_es() -> AsyncElasticsearch:
    """Fresh client owned by the caller, who must `await close()` it.

    For Celery tasks: each `asyncio.run()` runs on its own event loop and an
    async client's connection pool is bound to the loop that opened it, so the
    process singleton from `get_es()` cannot be shared across tasks."""
    return AsyncElasticsearch(
        hosts = [params.ES_HOST],
        basic_auth = domain.basic_auth(params.ES_USERNAME, params.ES_PASSWORD),
        verify_certs = params.ES_VERIFY_CERTS,
        request_timeout = params.TIMEOUT_S,
    )


def get_es() -> AsyncElasticsearch:
    """Process-bound singleton — one async client / connection pool reused."""
    global _client
    if _client is None:
        _client = new_es()
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
    """Idempotently create the configured YCS and BD indexes.

    Returns per-index status. Failures don't raise; they're best-effort
    with structured error reporting so the rest of the lifespan keeps going."""
    es = get_es()
    results: dict[str, dict] = {}
    for index, mapping in domain.index_pairs():
        try:
            results[index] = await ensure_index(es, index, mapping)
        except Exception as e:
            results[index] = {
                "created": False,
                "error": f"{type(e).__name__}: {e}",
            }
            logger.warning(
                f"[elasticsearch] ensure_index {index!r} failed: {e}"
            )
    return results


async def ensure_index(es: AsyncElasticsearch, index: str, mapping: dict) -> dict:
    """Create one configured index on the supplied client when it is missing."""
    if await es.indices.exists(index = index):
        return {"created": False, "reason": "exists"}
    try:
        await es.indices.create(
            index = index,
            mappings = mapping["mappings"],
            settings = mapping["settings"],
        )
    except Exception:
        # Concurrent book runs can race while creating the shared BD index.
        if not await es.indices.exists(index = index):
            raise
        return {"created": False, "reason": "exists"}
    logger.info(f"[elasticsearch] created index {index!r}")
    return {"created": True}


def span(operation: str, **attrs):
    """`db.elasticsearch.<operation>` client span around a search or index call; `attrs`
    are extra span attributes (`db.elasticsearch.index`, ...)."""
    return infra.opentelemetry.service.db_span("elasticsearch", operation, **attrs)
