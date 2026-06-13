"""Qdrant I/O for RR — `radar_papers` collection (paper-abstract vectors).

Per docs/CODE-CONVENTIONS.md §service: async + I/O. The collection is
created idempotently via `bootstrap_qdrant()` at FastAPI lifespan startup
(architecture doc §2.4.2).

Vector model: NIM `nvidia/llama-nemotron-embed-1b-v2` (2048d). Embedding
calls live in the LLM rotator (`embed_via_router_async`); this module
just persists the resulting vectors + payload.

Client reuse: `infra.qdrant.get_qdrant()` is the process-wide singleton
AsyncQdrantClient — no new HTTP/2 pool.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from infra.qdrant.service import get_qdrant
from ..entities import NormalizedPaper
from ..keys import (
    QDRANT_COLLECTION,
    QDRANT_PAYLOAD_ARXIV_ID,
    QDRANT_PAYLOAD_PUBLISHED,
    QDRANT_PAYLOAD_SIGNAL,
    QDRANT_PAYLOAD_SOURCES,
)
from ..params import STORES_PARAMS


logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Bootstrap — create the collection + payload indexes if missing. Idempotent;
# never `recreate_collection` (that drops data).
# --------------------------------------------------------------------------- #
async def bootstrap_qdrant() -> None:
    """Ensure `radar_papers` collection exists with the right vector config
    + payload indexes. Safe to re-run."""
    client = get_qdrant()
    exists = await client.collection_exists(collection_name=QDRANT_COLLECTION)
    if exists:
        logger.info(f"[rr-qdrant] collection {QDRANT_COLLECTION!r} already exists")
    else:
        await client.create_collection(
            collection_name = QDRANT_COLLECTION,
            vectors_config  = VectorParams(
                size     = STORES_PARAMS.qdrant_vector_dim,
                distance = Distance.COSINE,
            ),
            optimizers_config = OptimizersConfigDiff(
                default_segment_number = STORES_PARAMS.qdrant_segment_count,
            ),
        )
        logger.info(
            f"[rr-qdrant] created collection {QDRANT_COLLECTION!r} "
            f"(dim={STORES_PARAMS.qdrant_vector_dim}, distance=COSINE)"
        )
    # Payload indexes — idempotent (Qdrant ignores duplicates). These speed
    # up filter+search by 10-100× on the radar's typical queries.
    for field, schema in (
        (QDRANT_PAYLOAD_ARXIV_ID,  PayloadSchemaType.KEYWORD),
        (QDRANT_PAYLOAD_SIGNAL,    PayloadSchemaType.FLOAT),
        (QDRANT_PAYLOAD_PUBLISHED, PayloadSchemaType.DATETIME),
        (QDRANT_PAYLOAD_SOURCES,   PayloadSchemaType.KEYWORD),
    ):
        try:
            await client.create_payload_index(
                collection_name = QDRANT_COLLECTION,
                field_name      = field,
                field_schema    = schema,
            )
        except Exception as e:
            # Idempotency: an already-existing index raises in some
            # qdrant-client versions; log + continue.
            logger.debug(f"[rr-qdrant] payload index {field!r} exists or skip: {e}")


# --------------------------------------------------------------------------- #
# Point IDs — deterministic UUIDs from arxiv_id so re-upserts overwrite in
# place (Qdrant requires integer or UUID point ids; arxiv_id is a string).
# --------------------------------------------------------------------------- #
_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "rr.point.arxiv")


def _point_id(arxiv_id: str) -> str:
    """Deterministic UUIDv5 for the arxiv_id → repeat upserts are
    idempotent at the Qdrant layer."""
    return str(uuid5(_POINT_NAMESPACE, arxiv_id))


# --------------------------------------------------------------------------- #
# Upsert — one paper at a time (callers batch via persist_paper from the
# orchestrator). The signal goes into the payload so search results can
# be re-ranked by it without touching Postgres.
# --------------------------------------------------------------------------- #
async def upsert_paper_vector(
    paper: NormalizedPaper,
    *,
    embedding: list[float] | tuple[float, ...],
    signal: float | None = None,
) -> str:
    """Upsert a paper's vector + payload. Returns the point id.

    Pre-conditions: paper.arxiv_id must be non-None; embedding length must
    match QDRANT_VECTOR_DIM (validated by the qdrant client itself — we
    don't pre-check here)."""
    if not paper.arxiv_id:
        raise ValueError("[rr-qdrant] upsert_paper_vector requires arxiv_id != None")
    point = PointStruct(
        id      = _point_id(paper.arxiv_id),
        vector  = list(embedding),
        payload = {
            QDRANT_PAYLOAD_ARXIV_ID:  paper.arxiv_id,
            QDRANT_PAYLOAD_SIGNAL:    float(signal) if signal is not None else 0.0,
            QDRANT_PAYLOAD_PUBLISHED: paper.published.isoformat() if paper.published else None,
            QDRANT_PAYLOAD_SOURCES:   sorted(paper.sources),
            "title":                  paper.title,
            "authors":                list(paper.authors),
            "categories":             list(paper.categories),
            "citations":              int(paper.citations),
            "hn_points":              int(paper.hn_points),
            "hf_upvotes":             int(paper.hf_upvotes),
        },
    )
    client = get_qdrant()
    await client.upsert(collection_name=QDRANT_COLLECTION, points=[point])
    return point.id


# --------------------------------------------------------------------------- #
# Search — used by synthesis's NN-clustering pass + by the profile-editor
# UI's "similar papers" affordance (step 6).
# --------------------------------------------------------------------------- #
async def search_by_embedding(
    query_vector: list[float] | tuple[float, ...],
    *,
    limit: int = 20,
    arxiv_ids: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """k-NN search over `radar_papers`. When `arxiv_ids` is set, restricts
    to that subset (used by synthesis's "cluster these specific papers").
    Returns dicts {arxiv_id, score, payload}."""
    flt: Filter | None = None
    if arxiv_ids:
        flt = Filter(
            must=[
                FieldCondition(
                    key   = QDRANT_PAYLOAD_ARXIV_ID,
                    match = MatchValue(value=aid),
                )
                for aid in arxiv_ids
            ]
        )
    client = get_qdrant()
    results = await client.search(
        collection_name = QDRANT_COLLECTION,
        query_vector    = list(query_vector),
        query_filter    = flt,
        limit           = limit,
        with_payload    = True,
    )
    return [
        {
            "arxiv_id": r.payload.get(QDRANT_PAYLOAD_ARXIV_ID) if r.payload else None,
            "score":    r.score,
            "payload":  r.payload or {},
        }
        for r in results
    ]


async def count_points() -> int:
    """Total points in `radar_papers`. Cheap sanity check for bootstrap."""
    client = get_qdrant()
    info = await client.get_collection(collection_name=QDRANT_COLLECTION)
    return int(getattr(info, "points_count", 0) or 0)
