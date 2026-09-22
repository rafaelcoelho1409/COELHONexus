"""ycs/ingestion — streaming ES → Qdrant pipeline.

Imperative Shell — ES scroll, Qdrant collection bootstrap, embedding
dispatch, upsert. Pure projection in `domain.py`, point-id builder in
`keys.py`.
Memory-safe: never holds more than one transcript's chunks in memory
at a time."""
from __future__ import annotations
import domains, infra
from . import domain, keys, params

import json
import logging
from typing import Any, AsyncIterator, Callable

from elasticsearch import AsyncElasticsearch
from langchain_core.documents import Document
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    HnswConfigDiff,
    MatchAny,
    PointStruct,
    SparseIndexParams,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)
from redis.asyncio import Redis


logger = logging.getLogger(__name__)


async def ensure_collection(
    qdrant: AsyncQdrantClient, dense_dimensions: int, embedding_model: str = "",
    collection_name: str = params.QDRANT_COLLECTION,
) -> bool:
    """Idempotent collection create. Returns True only on first
    creation (False on a no-op).

    Hybrid schema = named dense `"dense"` slot + named sparse
    `"sparse"` slot. If a same-named collection exists but lacks
    either slot (e.g. a deprecated single-unnamed-vector collection
    from before the hybrid migration), we drop and recreate it.
    Without this guard, the legacy collection survives and every
    upsert fails with `Wrong input: Not existing vector name error:
    sparse` (HTTP 400).

    2026-09-13: `embedding_model` closes a gap the dimension check alone
    doesn't catch — the configured endpoint can now switch to a DIFFERENT
    model at the SAME dimension (e.g. two 2048-dim models), which would
    silently pass `dims_match` while mixing incomparable vectors in one
    cosine space. Sampled from an existing point's `embedding_model`
    payload field (set by `ingestion/service.py::_flush`), the same way
    `content_hash` is already sampled just below this function — no new
    storage mechanism, since Qdrant collections don't carry arbitrary
    custom metadata outside point payloads.

    2026-09-15: a model mismatch NO LONGER auto-drops the collection —
    that used to silently destroy every previously-ingested vector the
    instant the configured embedding model changed (live-confirmed: a
    whole corpus's Qdrant data gone, "a Rerun will rebuild this from
    scratch" was the only recovery path, no consent, no warning). The
    actual consent+re-embed flow now lives in
    `domains.ycs.embedding_migration` and gates dispatch BEFORE this
    function ever sees a mismatch in normal operation — reaching that
    state here means the gate was bypassed, so this raises instead of
    silently mixing incomparable vectors in one cosine space OR
    silently deleting data. `collection_name` defaults to the module
    constant (the stable alias — see that module's docstring) but the
    migration job passes its own staging collection name, which is
    always fresh/empty and never hits this branch.

    Dimension/schema mismatches (missing slot, wrong vector size) are
    UNCHANGED — still auto-recreated, since a collection that fails
    those checks has no valid data of ANY model to lose (wrong schema
    entirely, not "different model").

    2026-09-15: existence check switched from `get_collections()`
    (list of REAL collection names only — live-confirmed Qdrant never
    includes aliases in that listing) to `collection_exists()`, which
    resolves through an alias transparently. `params.QDRANT_COLLECTION` is a
    real collection today but becomes an alias the first time
    `embedding_migration` cuts one over — the old list-membership check
    would have silently stopped seeing it as existing at that point and
    tried (and failed) to create a real collection over the alias."""
    exists = await qdrant.collection_exists(collection_name)
    created = False
    if exists:
        info = await qdrant.get_collection(collection_name)
        vectors_cfg = info.config.params.vectors
        sparse_cfg  = info.config.params.sparse_vectors
        has_dense_slot = (
            isinstance(vectors_cfg, dict) and "dense" in vectors_cfg
        )
        has_sparse_slot = (
            isinstance(sparse_cfg, dict) and "sparse" in sparse_cfg
        )
        # Dimension check: an embedding-endpoint model change silently
        # passes the slot-name check but every upsert then 400s with a
        # vector-size mismatch. Vectors aren't comparable across models
        # anyway — recreate.
        dims_match = (
            has_dense_slot
            and getattr(vectors_cfg["dense"], "size", None) == dense_dimensions
        )
        model_match = True
        if has_dense_slot and dims_match and embedding_model:
            try:
                points, _ = await qdrant.scroll(
                    collection_name = collection_name,
                    limit = 1,
                    with_payload = ["embedding_model"],
                    with_vectors = False,
                )
                if points:
                    stored_model = (points[0].payload or {}).get("embedding_model")
                    # No stored value (pre-migration points) → can't prove a
                    # mismatch, don't force a recreate on that basis alone.
                    model_match = (not stored_model) or (stored_model == embedding_model)
            except Exception:
                pass  # collection-level trouble — dims_match check below still applies
        if not model_match:
            # 2026-09-15: no longer auto-drops (see this function's
            # docstring) — the consent+re-embed gate in
            # `domains.ycs.embedding_migration` should have caught this
            # BEFORE dispatch. Reaching it here means that gate was
            # bypassed; fail loud rather than silently mixing
            # incomparable vectors in one cosine space or deleting data.
            raise RuntimeError(
                f"[ycs:ingestion] collection {collection_name!r} holds "
                f"vectors from a different embedding model than the one "
                f"currently configured — refusing to write. This should "
                f"have been caught by the embedding-migration gate before "
                f"dispatch; if you're seeing this, that gate was bypassed "
                f"or the migration hasn't completed yet."
            )
        if not (has_dense_slot and has_sparse_slot and dims_match):
            # Wrong SCHEMA (missing vector slot, or a real dimension
            # mismatch) — not a model swap. A collection failing this has
            # no valid data of any model to lose, safe to auto-recreate.
            logger.warning(
                f"[ycs:ingestion] dropping collection {collection_name!r} "
                f"— schema mismatch (dense_slot={has_dense_slot}, "
                f"sparse_slot={has_sparse_slot}, dims_match={dims_match}); "
                f"recreating with hybrid schema."
            )
            await qdrant.delete_collection(collection_name)
            exists = False
    if not exists:
        await qdrant.create_collection(
            collection_name = collection_name,
            vectors_config = {
                "dense": VectorParams(
                    size = dense_dimensions,
                    distance = Distance.COSINE,
                    # 2026-09-15: explicit HNSW (was server defaults
                    # m=16/ef_construct=100). ef_construct=200 is the
                    # canonical production default — one-time build cost
                    # for a permanently better graph; m=16 unchanged
                    # (32 buys <1% recall at 2× RAM). Applies to future
                    # collections; existing ones keep their graph.
                    hnsw_config = HnswConfigDiff(
                        m = 16,
                        ef_construct = 200,
                    ),
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
            f"[ycs:ingestion] created collection {collection_name!r} "
            f"dim={dense_dimensions}"
        )
    # Payload keyword indexes — `video_id` backs the
    # per-video delete/skip filters, `channel_id` backs the Ask page's
    # channel pre-filter. Without them Qdrant falls back to full scans
    # once the corpus grows. Idempotent: re-creating an existing index
    # raises, which we swallow.
    for field in ("video_id", "channel_id"):
        try:
            await qdrant.create_payload_index(
                collection_name = collection_name,
                field_name      = field,
                field_schema    = "keyword",
            )
        except Exception:
            pass
    return created


async def _scroll_transcripts(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = params.SCROLL_BATCH_SIZE,
) -> AsyncIterator[dict]:
    """Async generator yielding transcript hits from the deprecated
    transcripts index. Uses ES scroll API so a 359+ result-set
    doesn't truncate on the 100-hit default."""
    query: dict = (
        {"terms": {"video_id": video_ids}} if video_ids
        else {"match_all": {}}
    )
    response = await es.search(
        index = infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
        query = query,
        size = batch_size,
        scroll = params.SCROLL_KEEPALIVE,
        # 2026-09-14: parent_video_id/part_index/part_total added for
        # the long-video splitter's partition-group completion
        # tracking (neo4j_task/qdrant_task need to know "is this a
        # partition, and how many siblings does it have") — omitted
        # before this, they'd silently read as None/missing downstream
        # even though the ES document actually carries them.
        _source = [
            "video_id", "content", "lang", "channel_id",
            "parent_video_id", "part_index", "part_total",
        ],
    )
    scroll_id = response.get("_scroll_id")
    hits = response["hits"]["hits"]
    try:
        while hits:
            for hit in hits:
                yield hit["_source"]
            response = await es.scroll(
                scroll_id = scroll_id, scroll = params.SCROLL_KEEPALIVE,
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
    `{video_id: metadata_dict}` — keyed by whatever id was PASSED IN,
    even though the actual `INDEX_METADATA` lookup resolves long-video
    partition ids (`"XYZ#p3"`) back to their parent (`"XYZ"`) first:
    yt-dlp metadata is only ever written once per real video, so a
    partition id has no entry of its own and would otherwise come back
    empty (blank title/channel on every partition's Video/Document
    node). Callers need no changes — same shape in, same shape out,
    just no longer silently empty for partitions."""
    if not video_ids:
        return {}
    lookup_ids = {vid: domain.parent_video_id(vid) for vid in video_ids}
    response = await es.search(
        index = infra.elasticsearch.keys.INDEX_METADATA,
        query = {"ids": {"values": list(set(lookup_ids.values()))}},
        size = len(set(lookup_ids.values())),
        _source = [
            "title", "channel", "channel_id", "upload_date", "webpage_url",
        ],
    )
    by_parent = {h["_id"]: h["_source"] for h in response["hits"]["hits"]}
    return {
        vid: by_parent[parent]
        for vid, parent in lookup_ids.items()
        if parent in by_parent
    }


async def fetch_transcripts_from_es(
    es: AsyncElasticsearch,
    video_ids: list[str] | None = None,
    batch_size: int = params.FETCH_BATCH_SIZE,
) -> list[dict]:
    """Non-streaming bulk fetch — used by `graph_builder` for the
    LLM-graph pass (small batches, full transcripts in memory)."""
    out: list[dict] = []
    async for transcript in _scroll_transcripts(es, video_ids, batch_size):
        out.append(transcript)
    return out


async def ingest_to_qdrant(
    es: AsyncElasticsearch,
    qdrant: AsyncQdrantClient,
    video_ids: list[str] | None = None,
    chunk_size: int = params.DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = params.DEFAULT_CHUNK_OVERLAP,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    collection_name: str = params.QDRANT_COLLECTION,
) -> dict:
    """Streaming pipeline: chunk → embed (dense NIM + sparse BM25) →
    upsert. Memory stays flat regardless of corpus size.

    Reworked (measured on the live cluster):

      - CROSS-VIDEO CHUNK PACKING. NIM embedding latency is per-CALL
        dominated (~11-15 s/call whether it carries 5 or 50 texts;
        measured 60.7 s per-video vs 11.2 s packed for the same 48
        chunks). Chunks now accumulate across videos and flush in
        params.FLUSH_CHUNKS groups — one NIM call per flush instead of one
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
    `progress_cb` (Wave 5 polish) receives per-transcript dicts so the
    Celery task wrapper can pipe them into `self.update_state(meta=)`."""
    dense_embeddings = domains.ycs.embeddings.service.create_dense_embeddings()
    sparse_embeddings = domains.ycs.embeddings.service.create_sparse_embeddings()
    dimensions, embedding_model = await domains.ycs.embeddings.service.get_embedding_info()

    collection_created = await ensure_collection(
        qdrant, dimensions, embedding_model, collection_name = collection_name,
    )

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
                    collection_name = collection_name,
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

    chunker = domains.ycs.chunker.domain.create_chunker(chunk_size, chunk_overlap)
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

    # Pack buffer — flushed every params.FLUSH_CHUNKS chunks. `pending_vids`
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
        dense_vectors = await dense_embeddings.aembed_documents(texts)
        sparse_vectors = list(sparse_embeddings.embed_documents(texts))
        # embedding_model recorded per point so ensure_collection's
        # model-identity guard can detect a live endpoint switch on the
        # next run (see that function's docstring) — sampled from the
        # SAME real calls above, not re-derived.
        model_used = dense_embeddings.last_model or ""
        points = [
            PointStruct(
                id = keys.point_id(
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
                payload = {**domain.build_payload(doc), "embedding_model": model_used},
            )
            for i, doc in enumerate(buffer)
        ]
        await qdrant.upsert(
            collection_name = collection_name, points = points,
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

        chunks = domains.ycs.chunker.domain.chunk_transcript(
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
                    collection_name = collection_name,
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
        if len(buffer) >= params.FLUSH_CHUNKS:
            await _flush()

        if total_transcripts % params.LOG_EVERY_N_TRANSCRIPTS == 0:
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
        # 2026-09-13: was hardcoded "nvidia-nim-api" — stale the moment
        # embeddings stopped being hardcoded to NIM. Reports whatever the
        # configured endpoint actually resolved to for this run.
        "embedding":           embedding_model or "(unknown)",
        "collection":          collection_name,
    }


async def expand_with_partition_ids(
    es: AsyncElasticsearch, video_ids: list[str],
) -> list[str]:
    """`video_ids` plus every known partition id (`"{vid}#p{n}"`) whose
    `parent_video_id` is one of them — looked up from
    `INDEX_TRANSCRIPTIONS`, which still has the mapping even for docs
    ingested before this function existed.

    2026-09-15: Qdrant point payloads carry `video_id` only (a split
    video's chunks are tagged with the PARTITION id) — unlike ES
    transcripts/Neo4j Documents, there's no `parent_video_id` payload
    field on a Qdrant point to OR against. `delete_points_for_videos`
    needs the FULL id set up front instead; this is that lookup,
    factored out so it stays correct for old data without a Qdrant
    payload schema change."""
    if not video_ids:
        return []
    try:
        resp = await es.search(
            index = infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
            size  = min(10000, max(200, len(video_ids) * 10)),
            # `.keyword`, not the bare field — `parent_video_id` is
            # mapped `text` (analyzed) with a `.keyword` sub-field for
            # exact matching (unlike `video_id`, which is pure
            # `keyword`); a `terms` query against the bare name
            # tokenizes and silently matches nothing. Verified live
            # against real partition data (2026-09-15).
            query = {"terms": {"parent_video_id.keyword": list(video_ids)}},
            _source = ["video_id"],
        )
        partition_ids = {
            h["_source"]["video_id"]
            for h in resp.get("hits", {}).get("hits", [])
            if h.get("_source", {}).get("video_id")
        }
    except Exception as e:
        logger.warning(
            f"[ycs:ingestion] partition-id expansion failed: "
            f"{type(e).__name__}: {str(e)[:200]} — proceeding with the "
            f"original {len(video_ids)} id(s) only"
        )
        return list(video_ids)
    return list(set(video_ids) | partition_ids)


async def _find_all_ycs_collections(qdrant: AsyncQdrantClient) -> list[str]:
    """Every REAL Qdrant collection that could hold YCS video data —
    the bare `params.QDRANT_COLLECTION` name (if it's still a literal, pre-
    migration collection) plus every versioned physical collection any
    past embedding-migration ever created
    (`domains.ycs.embedding_migration.domain.physical_collection_name`
    → `"{params.QDRANT_COLLECTION}__{model}__{dim}d"`).

    2026-09-15: added — `delete_points_for_videos` previously only
    swept the CURRENT alias target, silently leaving a video's vectors
    behind forever in any collection an earlier embedding migration
    superseded (those are deliberately kept around as a migration
    safety net, not deleted automatically — see that module's
    docstring — so they build up over time and need to be included
    here). Scoped by name prefix so this can never touch another
    project's collection sharing the same Qdrant instance (e.g.
    Research Radar's `radar_papers`)."""
    try:
        collections = await qdrant.get_collections()
    except Exception:
        return [params.QDRANT_COLLECTION]
    prefix = f"{params.QDRANT_COLLECTION}__"
    return [
        c.name for c in collections.collections
        if c.name == params.QDRANT_COLLECTION or c.name.startswith(prefix)
    ] or [params.QDRANT_COLLECTION]


async def delete_points_for_videos(
    qdrant:    AsyncQdrantClient,
    video_ids: list[str],
) -> dict[str, Any]:
    """Best-effort delete of every Qdrant point whose payload
    `video_id` is in `video_ids`, across EVERY collection any embedding
    migration ever created for YCS — not just the currently-active one.
    Used by the Pipeline panel's `Wipe cache` button and the Library's
    per-row/bulk delete.

    Uses a payload-filter selector (NOT point-id lookups) because
    point ids are `md5(video_id_chunk_index)` — we would need to know
    the chunk_index for every chunk, which we don't. The filter
    selector tells Qdrant "delete every point matching this filter,"
    which sweeps all chunks per video in one call, per collection.

    2026-09-15: sweeps every collection `_find_all_ycs_collections`
    finds, not just `params.QDRANT_COLLECTION` — a video ingested before an
    embedding-model migration has vectors in the OLD (superseded)
    physical collection too, which is kept around deliberately as a
    migration safety net; deleting a video must still remove it from
    there, or "delete this video" silently leaves stale copies of it
    behind under a retired model, forever, in a collection nothing else
    ever looks at again. Callers should pass `video_ids` already
    expanded with any known partition ids (see
    `expand_with_partition_ids`) — Qdrant payloads carry `video_id`
    only, never a `parent_video_id` to OR against.

    Best-effort per collection: one collection's error doesn't stop the
    sweep of the others, and never blocks the wipe of other stores."""
    if not video_ids:
        return {"qdrant_deleted": 0}
    target_collections = await _find_all_ycs_collections(qdrant)
    per_collection: dict[str, str] = {}
    errors: dict[str, str] = {}
    for name in target_collections:
        try:
            result = await qdrant.delete(
                collection_name = name,
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
            per_collection[name] = str(getattr(result, "status", "unknown"))
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
            logger.warning(
                f"[ycs:qdrant:wipe] {name} failed for "
                f"{len(video_ids)} videos: {errors[name]}"
            )
    logger.info(
        f"[ycs:qdrant:wipe] swept {len(target_collections)} "
        f"collection(s) for {len(video_ids)} video id(s) "
        f"(incl. any known partitions): {per_collection}"
    )
    out: dict[str, Any] = {
        "qdrant_deleted":             len(video_ids) if per_collection else 0,
        "qdrant_collections_swept":  per_collection,
    }
    if errors:
        out["qdrant_errors"] = errors
    return out


# Per-video Qdrant streaming buffer (2026-09-13: streaming counterpart to
# `ingest_to_qdrant` above. That function's cross-video chunk-packing
# buffer (`buffer`/`pending_vids`) only works because ONE task holds it
# in memory for the whole batch's lifetime. Once Qdrant ingestion is
# triggered per video from separate Celery task invocations
# (`qdrant_task/task.py::stream_video_to_qdrant`), the buffer has to live
# somewhere all of them can reach — Redis, not a Python list.
#
# Same embedding-latency-amortization rationale as the bulk path (NIM
# calls are per-CALL dominated, ~11-15s whether carrying 5 or 50 texts)
# — chunks still accumulate across videos and flush in `FLUSH_CHUNKS`
# groups, just via a Redis LIST instead of an in-memory one. The flush
# critical section is guarded by a Redis lock so two videos finishing
# near-simultaneously can't both pop + upsert the same chunks.


async def _already_current(
    qdrant: AsyncQdrantClient, video_id: str, content_hash: str,
) -> bool:
    """Same content-hash skip as the bulk path's pre-pass — a Rerun
    over an unchanged video shouldn't re-embed it. Best-effort: any
    lookup trouble falls through to "not current" (re-embed), never
    raises."""
    try:
        points, _ = await qdrant.scroll(
            collection_name = params.QDRANT_COLLECTION,
            scroll_filter = Filter(must = [
                FieldCondition(key = "video_id", match = MatchAny(any = [video_id])),
            ]),
            limit = 1,
            with_payload = ["content_hash"],
            with_vectors = False,
        )
    except Exception:
        return False
    return bool(
        points and (points[0].payload or {}).get("content_hash") == content_hash,
    )


async def _flush_buffer(
    redis: Redis, qdrant: AsyncQdrantClient, extract_id: str, *, drain_all: bool = False,
) -> int:
    """Lock-guarded pop-and-upsert. Returns points upserted (0 if
    another caller was already flushing, or the buffer was empty).

    2026-09-14: popped chunks are re-queued (front of the list, same
    order) if embedding/upsert fails — previously they were silently
    dropped, so 2 transient rotator blips on a 25-video run permanently
    lost those videos' points (22/24). A later video's flush or the
    final drain retries them. The embed call itself gets 3 attempts
    with backoff before giving up for this flush."""
    import asyncio as _asyncio

    from redis.asyncio.lock import Lock

    lock = Lock(
        redis, keys.qdrant_flush_lock_key(extract_id), timeout = 60, blocking_timeout = 0,
    )
    acquired = await lock.acquire()
    if not acquired:
        return 0  # someone else is flushing — fine, they'll drain what's there
    try:
        buffer_key = keys.qdrant_buffer_key(extract_id)
        if drain_all:
            count = await redis.llen(buffer_key)
            if count <= 0:
                return 0
        else:
            count = params.FLUSH_CHUNKS
            if await redis.llen(buffer_key) < params.FLUSH_CHUNKS:
                return 0
        raw_items = await redis.lpop(buffer_key, count)
        if not raw_items:
            return 0

        # 2026-09-14: mark this phase as actively draining for the
        # duration of the embed+upsert below — TTL is a self-healing
        # backstop if this process gets hard-killed (SIGTERM revoke)
        # before the `finally` gets to clear it. See `qdrant_draining_
        # key`'s docstring for why this exists: `get_phase_progress`
        # reads it so the bar doesn't report "Done" while this is
        # still running (that gap is what let a Stop click aimed at an
        # unrelated phase kill an in-flight drain with no warning).
        draining_key = keys.qdrant_draining_key(extract_id)
        await redis.set(draining_key, "1", ex = 120)

        async def _requeue(why: str) -> None:
            # Put popped chunks BACK at the front of the buffer (same
            # order) so a later flush or the final drain retries them
            # instead of losing them. Best-effort — logs and swallows.
            try:
                if raw_items:
                    await redis.lpush(buffer_key, *reversed(raw_items))
                    await redis.expire(buffer_key, params.STREAMING_KEY_TTL_S)
                    logger.warning(
                        f"[ycs:ingestion:streaming] {extract_id}: "
                        f"re-queued {len(raw_items)} chunks after {why}"
                    )
            except Exception as requeue_err:
                logger.warning(
                    f"[ycs:ingestion:streaming] {extract_id}: re-queue "
                    f"failed after {why}: "
                    f"{type(requeue_err).__name__}: {requeue_err}"
                )
        docs: list[Document] = []
        for raw in raw_items:
            text = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
            try:
                rec = json.loads(text)
                docs.append(Document(page_content = rec["page_content"], metadata = rec["metadata"]))
            except Exception as e:
                logger.warning(
                    f"[ycs:ingestion:streaming] dropped malformed buffer "
                    f"entry: {type(e).__name__}: {e}"
                )
        if not docs:
            return 0
        try:
            dense_embeddings = domains.ycs.embeddings.service.create_dense_embeddings()
            sparse_embeddings = domains.ycs.embeddings.service.create_sparse_embeddings()
            dimensions, embedding_model = await domains.ycs.embeddings.service.get_embedding_info()
            await ensure_collection(qdrant, dimensions, embedding_model)
        except Exception as setup_err:
            # Probe/collection failure happens AFTER the pop — without
            # a re-queue these chunks are lost (observed live:
            # `embed_probe_async timed out after 20s` dropped a whole
            # video's chunks). Re-queue and let a later flush retry.
            await _requeue(f"setup failure ({type(setup_err).__name__})")
            raise
        texts = [doc.page_content for doc in docs]
        last_err: Exception | None = None
        dense_vectors = None
        for attempt in range(3):
            try:
                dense_vectors = await dense_embeddings.aembed_documents(texts)
                last_err = None
                break
            except Exception as e:
                last_err = e
                logger.warning(
                    f"[ycs:ingestion:streaming] {extract_id}: embed "
                    f"attempt {attempt + 1}/3 failed "
                    f"({type(e).__name__}: {e}) — "
                    f"{len(docs)} chunks stay buffered for retry"
                )
                await _asyncio.sleep(2 * (attempt + 1))
        if dense_vectors is None:
            # All attempts failed — re-queue for a later flush / drain.
            await _requeue("embed failure")
            raise last_err
        sparse_vectors = list(sparse_embeddings.embed_documents(texts))
        model_used = dense_embeddings.last_model or ""
        points = [
            PointStruct(
                id = keys.point_id(doc.metadata["video_id"], doc.metadata["chunk_index"]),
                vector = {
                    "dense": dense_vectors[i],
                    "sparse": SparseVector(
                        indices = sparse_vectors[i].indices,
                        values =  sparse_vectors[i].values,
                    ),
                },
                payload = {**domain.build_payload(doc), "embedding_model": model_used},
            )
            for i, doc in enumerate(docs)
        ]
        try:
            await qdrant.upsert(collection_name = params.QDRANT_COLLECTION, points = points)
        except Exception:
            # An upsert failure must not lose already-popped chunks.
            await _requeue("upsert failure")
            raise
        logger.info(
            f"[ycs:ingestion:streaming] {extract_id}: flushed "
            f"{len(points)} points (drain_all={drain_all})"
        )
        return len(points)
    finally:
        # Clears unconditionally — a harmless no-op delete if we
        # returned before the flag was ever set (empty buffer, no
        # docs). Runs on every exit path (return OR raise) from the
        # try above, so a re-queued failure clears it exactly the same
        # as a clean flush.
        try:
            await redis.delete(keys.qdrant_draining_key(extract_id))
        except Exception:
            pass
        try:
            await lock.release()
        except Exception:
            pass


async def stream_video_to_qdrant(
    es:            AsyncElasticsearch,
    qdrant:        AsyncQdrantClient,
    redis:         Redis,
    video_id:      str,
    extract_id:    str,
    chunk_size:    int = params.DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = params.DEFAULT_CHUNK_OVERLAP,
) -> dict[str, Any]:
    """Chunk ONE video's transcript (fresh ES read — this task doesn't
    share the bulk path's prefetch) and push its chunks onto the run's
    shared Redis buffer, flushing whenever the buffer crosses
    `FLUSH_CHUNKS`. Stale-chunk sweep on content change, same as the
    bulk path (`ensure_collection`'s slot/dim checks happen inside
    `_flush_buffer`, only when a flush actually runs)."""
    transcripts = await fetch_transcripts_from_es(es, [video_id])
    if not transcripts:
        return {"video_id": video_id, "chunks": 0, "skipped": False, "error": "not found in ES"}
    transcript = transcripts[0] if isinstance(transcripts[0], dict) else {}
    # 2026-09-14: long-video partitioning — carried through every
    # return path below so `qdrant_task/task.py` can route this
    # partition's outcome through `mark_video_or_partition_done`
    # instead of directly bumping the phase counter. `None` for the
    # overwhelming majority (unsplit) videos.
    part_group = {
        "parent_video_id": transcript.get("parent_video_id"),
        "part_total":       transcript.get("part_total"),
    }
    metadata_map = await fetch_metadata_from_es(es, [video_id])
    meta = metadata_map.get(video_id, {})
    if not isinstance(meta, dict):
        meta = {}
    content = transcript.get("content") or ""
    content_hash = domain.content_hash(content)

    if await _already_current(qdrant, video_id, content_hash):
        logger.info(f"[ycs:ingestion:streaming] {video_id}: unchanged, skipping re-embed")
        return {"video_id": video_id, "chunks": 0, "skipped": True, **part_group}

    chunker = domains.ycs.chunker.domain.create_chunker(chunk_size, chunk_overlap)
    chunks = domains.ycs.chunker.domain.chunk_transcript(
        video_id = video_id,
        content = content,
        metadata = domain.build_chunk_metadata(
            lang =         transcript.get("lang", "en"),
            channel_id =   transcript.get("channel_id", ""),
            title =        meta.get("title", ""),
            channel =      meta.get("channel", ""),
            upload_date =  meta.get("upload_date", ""),
            webpage_url =  meta.get("webpage_url", ""),
            content_hash = content_hash,
        ),
        chunker = chunker,
    )
    if not chunks:
        return {"video_id": video_id, "chunks": 0, "skipped": False, **part_group}

    # Stale-chunk sweep — a shorter re-chunk could otherwise leave
    # orphans at high chunk_index values (same rationale as the bulk
    # path). Best-effort.
    try:
        await qdrant.delete(
            collection_name = params.QDRANT_COLLECTION,
            points_selector = FilterSelector(
                filter = Filter(must = [
                    FieldCondition(key = "video_id", match = MatchAny(any = [video_id])),
                ]),
            ),
        )
    except Exception:
        pass

    buffer_key = keys.qdrant_buffer_key(extract_id)
    records = [
        json.dumps({"page_content": doc.page_content, "metadata": doc.metadata})
        for doc in chunks
    ]
    await redis.rpush(buffer_key, *records)
    await redis.expire(buffer_key, params.STREAMING_KEY_TTL_S)
    points_flushed = await _flush_buffer(redis, qdrant, extract_id)
    return {
        "video_id":       video_id,
        "chunks":         len(chunks),
        "skipped":        False,
        "points_flushed": points_flushed,
        **part_group,
    }


async def finalize_qdrant_buffer(
    redis: Redis, qdrant: AsyncQdrantClient, extract_id: str,
) -> int:
    """Called by whichever per-video task turns out to be the LAST one
    for the Qdrant phase (see `pipeline_task.service.mark_video_done`)
    — drains any remainder below the normal `FLUSH_CHUNKS` threshold so
    the tail of a run isn't silently left unembedded."""
    return await _flush_buffer(redis, qdrant, extract_id, drain_all = True)

