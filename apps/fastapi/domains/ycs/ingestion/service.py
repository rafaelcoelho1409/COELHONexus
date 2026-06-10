"""ycs/ingestion — streaming ES → Qdrant pipeline.

Imperative Shell — ES scroll, Qdrant collection bootstrap, embedding
dispatch, upsert. Pure projection in `domain.py`, point-id builder in
`keys.py`.

Direct port of deprecated `services/youtube/ingestion.py:L49-264`.
Memory-safe: never holds more than one transcript's chunks in memory
at a time."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Callable

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchAny,
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
    FLUSH_CHUNKS,
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
    creation (False on a no-op).

    Hybrid schema = named dense `"dense"` slot + named sparse
    `"sparse"` slot. If a same-named collection exists but lacks
    either slot (e.g. a deprecated single-unnamed-vector collection
    from before the hybrid migration), we drop and recreate it.
    Without this guard, the legacy collection survives and every
    upsert fails with `Wrong input: Not existing vector name error:
    sparse` (HTTP 400)."""
    collections = await qdrant.get_collections()
    existing = {c.name for c in collections.collections}
    created = False
    if QDRANT_COLLECTION in existing:
        info = await qdrant.get_collection(QDRANT_COLLECTION)
        vectors_cfg = info.config.params.vectors
        sparse_cfg  = info.config.params.sparse_vectors
        has_dense_slot = (
            isinstance(vectors_cfg, dict) and "dense" in vectors_cfg
        )
        has_sparse_slot = (
            isinstance(sparse_cfg, dict) and "sparse" in sparse_cfg
        )
        # Dimension check (2026-06-10): an embedder-model change (env
        # `NVIDIA_EMBEDDING_MODEL`) silently passes the slot-name check
        # but every upsert then 400s with a vector-size mismatch.
        # Vectors aren't comparable across models anyway — recreate.
        dims_match = (
            has_dense_slot
            and getattr(vectors_cfg["dense"], "size", None) == dense_dimensions
        )
        if not (has_dense_slot and has_sparse_slot and dims_match):
            # Wrong-schema collection found. Drop + recreate. The points
            # inside were built against the legacy schema and can't be
            # rewritten in place; downstream Phase A → ES indexing is the
            # source of truth, so a Rerun will rebuild this from scratch.
            logger.warning(
                f"[ycs:ingestion] dropping collection {QDRANT_COLLECTION!r} "
                f"— schema mismatch (dense_slot={has_dense_slot}, "
                f"sparse_slot={has_sparse_slot}, dims_match={dims_match}); "
                f"recreating with hybrid schema."
            )
            await qdrant.delete_collection(QDRANT_COLLECTION)
            existing.discard(QDRANT_COLLECTION)
    if QDRANT_COLLECTION not in existing:
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
        created = True
        logger.info(
            f"[ycs:ingestion] created collection {QDRANT_COLLECTION!r} "
            f"dim={dense_dimensions}"
        )
    # Payload keyword indexes (2026-06-10) — `video_id` backs the
    # per-video delete/skip filters, `channel_id` backs the Ask page's
    # channel pre-filter. Without them Qdrant falls back to full scans
    # once the corpus grows. Idempotent: re-creating an existing index
    # raises, which we swallow.
    for field in ("video_id", "channel_id"):
        try:
            await qdrant.create_payload_index(
                collection_name = QDRANT_COLLECTION,
                field_name      = field,
                field_schema    = "keyword",
            )
        except Exception:
            pass
    return created


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
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
) -> dict:
    """Streaming pipeline: chunk → embed (dense NIM + sparse BM25) →
    upsert. Memory stays flat regardless of corpus size.

    2026-06-10 REWORK (measured on the live cluster):

      - CROSS-VIDEO CHUNK PACKING. NIM embedding latency is per-CALL
        dominated (~11-15 s/call whether it carries 5 or 50 texts;
        measured 60.7 s per-video vs 11.2 s packed for the same 48
        chunks). Chunks now accumulate across videos and flush in
        FLUSH_CHUNKS groups — one NIM call per flush instead of one
        per video (5.4× on the embed stage).
      - CONTENT-HASH SKIP. Every point carries `content_hash` (md5 of
        the full transcript). A pre-pass compares the stored hash per
        video; unchanged videos skip chunk+embed+upsert entirely, so a
        Rerun over a mostly-ingested corpus costs seconds, not a full
        re-embed (point ids are deterministic, so the old behavior was
        a correct but wasteful full overwrite).
      - STALE-CHUNK SWEEP. When content DID change, the video's old
        points are deleted by filter before re-upsert — previously a
        shrunken transcript left orphan chunks (old chunk_index beyond
        the new total) in the collection forever.
      - Bulk metadata prefetch (one ES query for all ids, was one per
        video).

    Mirror of deprecated `services/youtube/ingestion.py:L148-264`.
    `progress_cb` (Wave 5 polish) receives per-transcript dicts so the
    Celery task wrapper can pipe them into `self.update_state(meta=)`."""
    dense_embeddings = create_dense_embeddings()
    sparse_embeddings = create_sparse_embeddings()
    dimensions = get_embedding_dimensions()

    collection_created = await ensure_collection(qdrant, dimensions)

    # Two-phase: enumerate transcripts first (fast — text only, ~5s
    # for 359 transcripts), THEN embed (slow — API calls). Separating
    # phases keeps the ES scroll context from expiring during the
    # long embedding phase.
    if progress_cb:
        progress_cb({"phase": "scroll", "current": 0, "total": 0})
    all_transcripts: list[dict] = []
    async for transcript in _scroll_transcripts(es, video_ids):
        all_transcripts.append(transcript)

    # Bulk metadata prefetch — one ES query for every video in the run.
    all_ids = list({t["video_id"] for t in all_transcripts})
    metadata_cache = await fetch_metadata_from_es(es, all_ids)

    # Hash pre-pass — fetch ONE stored point per video (payload-only,
    # indexed filter) and compare content hashes. `not collection_
    # created` guard skips the N lookups on a fresh collection.
    skip_vids: set[str] = set()
    hashes = {
        t["video_id"]: domain.content_hash(t.get("content") or "")
        for t in all_transcripts
    }
    if not collection_created:
        for vid in all_ids:
            try:
                points, _ = await qdrant.scroll(
                    collection_name = QDRANT_COLLECTION,
                    scroll_filter = Filter(must = [
                        FieldCondition(
                            key = "video_id", match = MatchAny(any = [vid]),
                        ),
                    ]),
                    limit = 1,
                    with_payload = ["content_hash"],
                    with_vectors = False,
                )
            except Exception:
                break  # collection-level trouble — fall back to full ingest
            if points and (points[0].payload or {}).get(
                "content_hash",
            ) == hashes[vid]:
                skip_vids.add(vid)
    if skip_vids:
        logger.info(
            f"[ycs:ingestion] {len(skip_vids)}/{len(all_ids)} videos "
            f"unchanged (content_hash match) — skipping re-embed"
        )

    if progress_cb:
        progress_cb({
            "phase":   "embedding",
            "current": 0,
            "total":   len(all_transcripts),
            "chunks":  0,
            "points":  0,
        })

    chunker = create_chunker(chunk_size, chunk_overlap)
    total_transcripts = 0
    total_chunks = 0
    total_upserted = 0
    # Per-video status tracking for the Ingest-page right-column
    # list — Qdrant upserts don't fail per-video (the whole task
    # either succeeds or raises), so `failed_ids` stays empty here.
    completed_ids: list[str] = []

    def _emit_progress(vid: str) -> None:
        if not progress_cb:
            return
        meta = metadata_cache.get(vid, {})
        progress_cb({
            "phase":         "embedding",
            "current":       total_transcripts,
            "total":         len(all_transcripts),
            "chunks":        total_chunks,
            "points":        total_upserted,
            "completed_ids": list(completed_ids),
            "failed_ids":    [],
            "current_item": {
                "id":         vid,
                "title":      meta.get("title", ""),
                "channel":    meta.get("channel", ""),
                "channel_id": meta.get("channel_id", ""),
            },
        })

    # Pack buffer — flushed every FLUSH_CHUNKS chunks. `pending_vids`
    # tracks which videos' chunks are inside the un-flushed buffer so
    # completed_ids only advances once a video's points are actually
    # in Qdrant.
    buffer: list = []          # Documents awaiting embed+upsert
    pending_vids: list[str] = []

    async def _flush() -> None:
        nonlocal total_upserted
        if not buffer:
            return
        texts = [doc.page_content for doc in buffer]
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
            for i, doc in enumerate(buffer)
        ]
        await qdrant.upsert(
            collection_name = QDRANT_COLLECTION, points = points,
        )
        total_upserted += len(points)
        buffer.clear()
        for vid in pending_vids:
            if vid not in completed_ids:
                completed_ids.append(vid)
        last_vid = pending_vids[-1] if pending_vids else ""
        pending_vids.clear()
        if last_vid:
            _emit_progress(last_vid)

    for transcript in all_transcripts:
        vid = transcript["video_id"]
        total_transcripts += 1
        if vid in skip_vids:
            # Unchanged — count as completed without touching it.
            if vid not in completed_ids:
                completed_ids.append(vid)
            _emit_progress(vid)
            continue
        meta = metadata_cache.get(vid, {})

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
                content_hash = hashes[vid],
            ),
            chunker = chunker,
        )
        if not chunks:
            continue
        total_chunks += len(chunks)
        # Stale-chunk sweep — the video changed (or was never hashed):
        # drop its old points so a shorter re-chunk can't leave
        # orphans at high chunk_index values.
        if not collection_created:
            try:
                await qdrant.delete(
                    collection_name = QDRANT_COLLECTION,
                    points_selector = FilterSelector(
                        filter = Filter(must = [
                            FieldCondition(
                                key = "video_id",
                                match = MatchAny(any = [vid]),
                            ),
                        ]),
                    ),
                )
            except Exception:
                pass
        buffer.extend(chunks)
        pending_vids.append(vid)
        if len(buffer) >= FLUSH_CHUNKS:
            await _flush()

        if total_transcripts % LOG_EVERY_N_TRANSCRIPTS == 0:
            logger.info(
                f"[ycs:ingestion] progress: {total_transcripts} "
                f"transcripts, {total_chunks} chunks, "
                f"{total_upserted} points"
            )

    await _flush()

    return {
        "total_transcripts":   total_transcripts,
        "total_chunks":        total_chunks,
        "points_upserted":     total_upserted,
        "videos_unchanged":    len(skip_vids),
        "collection_created":  collection_created,
        "embedding":           "nvidia-nim-api",
        "collection":          QDRANT_COLLECTION,
    }


async def delete_points_for_videos(
    qdrant:    AsyncQdrantClient,
    video_ids: list[str],
) -> dict[str, Any]:
    """Best-effort delete of every Qdrant point whose payload
    `video_id` is in `video_ids`. Used by the Pipeline panel's
    `Wipe cache` button.

    Uses a payload-filter selector (NOT point-id lookups) because
    point ids are `md5(video_id_chunk_index)` — we would need to know
    the chunk_index for every chunk, which we don't. The filter
    selector tells Qdrant "delete every point matching this filter,"
    which sweeps all chunks per video in one call.

    Best-effort: collection-missing or Qdrant-down errors are logged
    + counted, never raised — the wipe of other stores still happens."""
    if not video_ids:
        return {"qdrant_deleted": 0}
    try:
        result = await qdrant.delete(
            collection_name = QDRANT_COLLECTION,
            points_selector = FilterSelector(
                filter = Filter(
                    must = [
                        FieldCondition(
                            key = "video_id",
                            match = MatchAny(any = list(video_ids)),
                        ),
                    ],
                ),
            ),
            wait = True,
        )
        status_str = str(getattr(result, "status", "unknown"))
        logger.info(
            f"[ycs:qdrant:wipe] collection={QDRANT_COLLECTION} "
            f"status={status_str} video_ids={len(video_ids)}"
        )
        return {
            "qdrant_deleted": len(video_ids),
            "qdrant_status":  status_str,
        }
    except Exception as e:
        logger.warning(
            f"[ycs:qdrant:wipe] failed for {len(video_ids)} videos: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )
        return {"qdrant_deleted": 0, "qdrant_error": str(e)[:200]}

