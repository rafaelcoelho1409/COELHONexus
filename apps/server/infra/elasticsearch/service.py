"""Async Elasticsearch client factory + alias/index bootstrap.

Lives at the infra layer (mirror of `infra/qdrant/`) so multiple domains
can share a single cluster client. YCS uses metadata and transcriptions
indexes; BD Ingestion uses its processed-page index.

The only module that imports the `elasticsearch` library or reads
`ELASTICSEARCH_*` env vars: the FastAPI process uses the `get_es()`
singleton, Celery tasks open their own client with `new_es()`, and the
`AsyncElasticsearch` type is reached dotted through here. Each stable alias
points to one configured versioned physical index.

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
    """Idempotently create the configured YCS and BD indexes and aliases.

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
    """Create a versioned physical index and attach its stable alias.

    Existing aliases are accepted only when they target the configured physical
    version. A concrete index occupying the alias name is reported explicitly;
    it must be migrated before the alias can be created.
    """
    physical_index = domain.keys.PHYSICAL_INDEX_BY_ALIAS.get(index)
    if not physical_index:
        raise ValueError(f"Elasticsearch alias {index!r} has no configured physical index")

    if await es.indices.exists_alias(name = index):
        aliases = await es.indices.get_alias(name = index)
        if set(aliases) != {physical_index}:
            raise RuntimeError(
                f"Elasticsearch alias {index!r} targets {sorted(aliases)!r}; "
                f"expected only {physical_index!r}"
            )
        alias_config = aliases[physical_index].get("aliases", {}).get(index, {})
        if alias_config.get("is_write_index") is not True:
            await es.indices.put_alias(
                index = physical_index,
                name = index,
                is_write_index = True,
            )
        return {"created": False, "reason": "alias exists", "physical_index": physical_index}

    if await es.indices.exists(index = index):
        raise RuntimeError(
            f"Elasticsearch name {index!r} is an existing concrete index; migrate it before alias creation"
        )

    created = False
    if not await es.indices.exists(index = physical_index):
        try:
            await es.indices.create(
                index = physical_index,
                mappings = mapping["mappings"],
                settings = mapping["settings"],
            )
            created = True
        except Exception:
            if not await es.indices.exists(index = physical_index):
                raise

    try:
        await es.indices.put_alias(index = physical_index, name = index, is_write_index = True)
    except Exception:
        # Concurrent application/worker startup can race to attach the same alias.
        if not await es.indices.exists_alias(name = index):
            raise
    logger.info(
        f"[elasticsearch] {'created' if created else 'attached'} alias {index!r} "
        f"to physical index {physical_index!r}"
    )
    return {"created": created, "physical_index": physical_index}


def span(operation: str, **attrs):
    """`db.elasticsearch.<operation>` client span around a search or index call; `attrs`
    are extra span attributes (`db.elasticsearch.index`, ...)."""
    return infra.opentelemetry.service.db_span("elasticsearch", operation, **attrs)
