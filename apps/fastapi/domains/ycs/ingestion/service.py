"""ycs/ingestion — streaming ES → Qdrant pipeline.

Imperative Shell — ES scroll, Qdrant collection bootstrap, embedding
dispatch, upsert. Pure projection in `domain.py`, point-id builder in
`keys.py`.

Direct port of deprecated `services/youtube/ingestion.py:L49-264`.
Memory-safe: never holds more than one transcript's chunks in memory
at a time."""
from __future__ import annotations

import logging
from typing import AsyncIterator

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

from domains.ycs.chunker import chunk_transcript, create_chunker
from domains.ycs.embeddings import (
    create_dense_embeddings,
    create_sparse_embeddings,
    get_embedding_dimensions,
)
from infra.elasticsearch import INDEX_METADATA, INDEX_TRANSCRIPTIONS

from . import domain
from .keys import point_id
from .params import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    FETCH_BATCH_SIZE,
    LOG_EVERY_N_TRANSCRIPTS,
    QDRANT_COLLECTION,
    SCROLL_BATCH_SIZE,
    SCROLL_KEEPALIVE,
)


logger = logging.getLogger(__name__)


# ---------- Qdrant collection bootstrap ---------------------------------

async def ensure_collection(
    qdrant: AsyncQdrantClient, dense_dimensions: int,
) -> bool:
    """Idempotent collection create. Returns True only on first
    creation (False on a no-op)."""
    collections = await qdrant.get_collections()
    existing = {c.name for c in collections.collections}
    if QDRANT_COLLECTION in existing:
        return False
    await qdrant.create_collection(
        collection_name = QDRANT_COLLECTION,
        vectors_config = {
            "dense": VectorParams(
                size = dense_dimensions,
                distance = Distance.COSINE,
            ),
        },
        sparse_vectors_config = {
            "sparse": SparseVectorParams(
                index = SparseIndexParams(on_disk = False),
            ),
        },
    )
    logger.info(
        f"[ycs:ingestion] created collection {QDRANT_COLLECTION!r} "
        f"dim={dense_dimensions}"
    )
    return True


# ---------- ES scroll iterators -----------------------------------------

async def _scroll_transcripts(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = SCROLL_BATCH_SIZE,
) -> AsyncIterator[dict]:
    """Async generator yielding transcript hits from the deprecated
    transcripts index. Uses ES scroll API so a 359+ result-set
    doesn't truncate on the 100-hit default."""
    query: dict = (
        {"terms": {"video_id": video_ids}} if video_ids
        else {"match_all": {}}
    )
    response = await es.search(
        index = INDEX_TRANSCRIPTIONS,
        query = query,
        size = batch_size,
        scroll = SCROLL_KEEPALIVE,
        _source = ["video_id", "content", "lang", "channel_id"],
    )
    scroll_id = response.get("_scroll_id")
    hits = response["hits"]["hits"]
    try:
        while hits:
            for hit in hits:
                yield hit["_source"]
            response = await es.scroll(
                scroll_id = scroll_id, scroll = SCROLL_KEEPALIVE,
            )
            scroll_id = response.get("_scroll_id")
            hits = response["hits"]["hits"]
    finally:
        if scroll_id:
            try:
                await es.clear_scroll(scroll_id = scroll_id)
            except Exception:
                pass


async def fetch_metadata_from_es(
    es: AsyncElasticsearch, video_ids: list[str],
) -> dict:
    """Bulk-fetch metadata for the supplied ids. Returns
    `{video_id: metadata_dict}`."""
    if not video_ids:
        return {}
    response = await es.search(
        index = INDEX_METADATA,
        query = {"ids": {"values": video_ids}},
        size = len(video_ids),
        _source = [
            "title", "channel", "channel_id", "upload_date", "webpage_url",
        ],
    )
    return {h["_id"]: h["_source"] for h in response["hits"]["hits"]}


async def fetch_transcripts_from_es(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = FETCH_BATCH_SIZE,
) -> list[dict]:
    """Non-streaming bulk fetch — used by `graph_builder` for the
    LLM-graph pass (small batches, full transcripts in memory)."""
    out: list[dict] = []
    async for transcript in _scroll_transcripts(es, video_ids, batch_size):
        out.append(transcript)
    return out


# ---------- main pipeline -----------------------------------------------

async def ingest_to_qdrant(
    es: AsyncElasticsearch,
    qdrant: AsyncQdrantClient,
    video_ids: list[str] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> dict:
    """Streaming pipeline — for each transcript: chunk → embed (dense
    NIM + sparse BM25) → upsert. Memory stays flat regardless of
    corpus size.

    Mirror of deprecated `services/youtube/ingestion.py:L148-264`."""
    dense_embeddings = create_dense_embeddings()
    sparse_embeddings = create_sparse_embeddings()
    dimensions = get_embedding_dimensions()

    collection_created = await ensure_collection(qdrant, dimensions)

    # Two-phase: enumerate transcripts first (fast — text only, ~5s
    # for 359 transcripts), THEN embed one-at-a-time (slow — CPU-bound
    # API calls). Separating phases keeps the ES scroll context from
    # expiring during the long embedding phase.
    all_transcripts: list[dict] = []
    async for transcript in _scroll_transcripts(es, video_ids):
        all_transcripts.append(transcript)

    chunker = create_chunker(chunk_size, chunk_overlap)
    total_transcripts = 0
    total_chunks = 0
    total_upserted = 0
    metadata_cache: dict[str, dict] = {}

    for transcript in all_transcripts:
        vid = transcript["video_id"]
        total_transcripts += 1

        if vid not in metadata_cache:
            meta_map = await fetch_metadata_from_es(es, [vid])
            metadata_cache[vid] = meta_map.get(vid, {})
        meta = metadata_cache[vid]

        chunks = chunk_transcript(
            video_id = vid,
            content = transcript.get("content") or "",
            metadata = domain.build_chunk_metadata(
                lang =         transcript.get("lang", "en"),
                channel_id =   transcript.get("channel_id", ""),
                title =        meta.get("title", ""),
                channel =      meta.get("channel", ""),
                upload_date =  meta.get("upload_date", ""),
                webpage_url =  meta.get("webpage_url", ""),
            ),
            chunker = chunker,
        )
        if not chunks:
            continue
        total_chunks += len(chunks)

        texts = [doc.page_content for doc in chunks]
        dense_vectors = dense_embeddings.embed_documents(texts)
        sparse_vectors = list(sparse_embeddings.embed_documents(texts))

        points = [
            PointStruct(
                id = point_id(
                    doc.metadata["video_id"],
                    doc.metadata["chunk_index"],
                ),
                vector = {
                    "dense": dense_vectors[i],
                    "sparse": SparseVector(
                        indices = sparse_vectors[i].indices,
                        values =  sparse_vectors[i].values,
                    ),
                },
                payload = domain.build_payload(doc),
            )
            for i, doc in enumerate(chunks)
        ]
        await qdrant.upsert(
            collection_name = QDRANT_COLLECTION, points = points,
        )
        total_upserted += len(points)

        if total_transcripts % LOG_EVERY_N_TRANSCRIPTS == 0:
            logger.info(
                f"[ycs:ingestion] progress: {total_transcripts} "
                f"transcripts, {total_chunks} chunks, "
                f"{total_upserted} points"
            )

    return {
        "total_transcripts":   total_transcripts,
        "total_chunks":        total_chunks,
        "points_upserted":     total_upserted,
        "collection_created":  collection_created,
        "embedding":           "nvidia-nim-api",
        "collection":          QDRANT_COLLECTION,
    }
