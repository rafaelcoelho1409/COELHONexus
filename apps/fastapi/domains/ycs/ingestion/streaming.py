"""ycs/ingestion — per-video Qdrant streaming buffer (Imperative Shell).

2026-09-13: streaming counterpart to `service.py::ingest_to_qdrant`.
That function's cross-video chunk-packing buffer (`buffer`/
`pending_vids`) only works because ONE task holds it in memory for the
whole batch's lifetime. Once Qdrant ingestion is triggered per video
from separate Celery task invocations (`qdrant_task/task.py::
stream_video_to_qdrant`), the buffer has to live somewhere all of them
can reach — Redis, not a Python list.

Same embedding-latency-amortization rationale as the bulk path (NIM
calls are per-CALL dominated, ~11-15s whether carrying 5 or 50 texts)
— chunks still accumulate across videos and flush in `FLUSH_CHUNKS`
groups, just via a Redis LIST instead of an in-memory one. The flush
critical section is guarded by a Redis lock so two videos finishing
near-simultaneously can't both pop + upsert the same chunks."""
from __future__ import annotations

import json
import logging
from typing import Any

from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    FieldCondition,
    Filter,
    MatchAny,
    PointStruct,
    SparseVector,
)
from langchain_core.documents import Document
from redis.asyncio import Redis
from redis.asyncio.lock import Lock

from domains.ycs.chunker import chunk_transcript, create_chunker
from domains.ycs.embeddings import (
    create_dense_embeddings,
    create_sparse_embeddings,
    get_embedding_info,
)

from . import domain
from .keys import point_id, qdrant_buffer_key, qdrant_draining_key, qdrant_flush_lock_key
from .params import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    FLUSH_CHUNKS,
    QDRANT_COLLECTION,
    STREAMING_KEY_TTL_S,
)
from .service import ensure_collection, fetch_metadata_from_es, fetch_transcripts_from_es


logger = logging.getLogger(__name__)


async def _already_current(
    qdrant: AsyncQdrantClient, video_id: str, content_hash: str,
) -> bool:
    """Same content-hash skip as the bulk path's pre-pass — a Rerun
    over an unchanged video shouldn't re-embed it. Best-effort: any
    lookup trouble falls through to "not current" (re-embed), never
    raises."""
    try:
        points, _ = await qdrant.scroll(
            collection_name = QDRANT_COLLECTION,
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

    lock = Lock(
        redis, qdrant_flush_lock_key(extract_id), timeout = 60, blocking_timeout = 0,
    )
    acquired = await lock.acquire()
    if not acquired:
        return 0  # someone else is flushing — fine, they'll drain what's there
    try:
        buffer_key = qdrant_buffer_key(extract_id)
        if drain_all:
            count = await redis.llen(buffer_key)
            if count <= 0:
                return 0
        else:
            count = FLUSH_CHUNKS
            if await redis.llen(buffer_key) < FLUSH_CHUNKS:
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
        draining_key = qdrant_draining_key(extract_id)
        await redis.set(draining_key, "1", ex = 120)

        async def _requeue(why: str) -> None:
            # Put popped chunks BACK at the front of the buffer (same
            # order) so a later flush or the final drain retries them
            # instead of losing them. Best-effort — logs and swallows.
            try:
                if raw_items:
                    await redis.lpush(buffer_key, *reversed(raw_items))
                    await redis.expire(buffer_key, STREAMING_KEY_TTL_S)
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
            dense_embeddings = create_dense_embeddings()
            sparse_embeddings = create_sparse_embeddings()
            dimensions, embedding_model = await get_embedding_info()
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
                id = point_id(doc.metadata["video_id"], doc.metadata["chunk_index"]),
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
            await qdrant.upsert(collection_name = QDRANT_COLLECTION, points = points)
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
            await redis.delete(qdrant_draining_key(extract_id))
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
    chunk_size:    int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
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
    metadata_map = await fetch_metadata_from_es(es, [video_id])
    meta = metadata_map.get(video_id, {})
    if not isinstance(meta, dict):
        meta = {}
    content = transcript.get("content") or ""
    content_hash = domain.content_hash(content)

    if await _already_current(qdrant, video_id, content_hash):
        logger.info(f"[ycs:ingestion:streaming] {video_id}: unchanged, skipping re-embed")
        return {"video_id": video_id, "chunks": 0, "skipped": True}

    chunker = create_chunker(chunk_size, chunk_overlap)
    chunks = chunk_transcript(
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
        return {"video_id": video_id, "chunks": 0, "skipped": False}

    # Stale-chunk sweep — a shorter re-chunk could otherwise leave
    # orphans at high chunk_index values (same rationale as the bulk
    # path). Best-effort.
    try:
        from qdrant_client.http.models import FilterSelector
        await qdrant.delete(
            collection_name = QDRANT_COLLECTION,
            points_selector = FilterSelector(
                filter = Filter(must = [
                    FieldCondition(key = "video_id", match = MatchAny(any = [video_id])),
                ]),
            ),
        )
    except Exception:
        pass

    buffer_key = qdrant_buffer_key(extract_id)
    records = [
        json.dumps({"page_content": doc.page_content, "metadata": doc.metadata})
        for doc in chunks
    ]
    await redis.rpush(buffer_key, *records)
    await redis.expire(buffer_key, STREAMING_KEY_TTL_S)
    points_flushed = await _flush_buffer(redis, qdrant, extract_id)
    return {
        "video_id":       video_id,
        "chunks":         len(chunks),
        "skipped":        False,
        "points_flushed": points_flushed,
    }


async def finalize_qdrant_buffer(
    redis: Redis, qdrant: AsyncQdrantClient, extract_id: str,
) -> int:
    """Called by whichever per-video task turns out to be the LAST one
    for the Qdrant phase (see `pipeline_task.streaming.mark_video_done`)
    — drains any remainder below the normal `FLUSH_CHUNKS` threshold so
    the tail of a run isn't silently left unembedded."""
    return await _flush_buffer(redis, qdrant, extract_id, drain_all = True)
