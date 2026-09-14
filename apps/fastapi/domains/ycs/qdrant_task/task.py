"""ycs/qdrant_task — ES transcripts → chunk → embed → Qdrant upsert + cache invalidate."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import redis.asyncio as redis_aio
from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

from domains.ycs.cache import invalidate_cache as _invalidate_cache
from domains.ycs.ingestion import ingest_to_qdrant as run_ingestion
from infra.celery import app


logger = get_task_logger(__name__)


@app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.ingest_to_qdrant",
)
def ingest_to_qdrant(
    self,
    video_ids:     list[str] | None = None,
    chunk_size:    int              = 2000,
    chunk_overlap: int              = 200,
) -> dict[str, Any]:
    """Stream ES transcripts → chunk → embed → Qdrant upsert."""
    logger.info(
        f"[ingest_to_qdrant] Starting: video_ids={video_ids}, "
        f"chunk_size={chunk_size}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    async def _run() -> dict[str, Any]:
        from infra.langfuse import (
            set_current_span_langfuse_io,
            set_current_span_langfuse_observation_metadata,
            set_current_span_langfuse_trace_metadata,
        )
        from infra.langfuse.sessions import session as _lf_session
        from infra.otel import get_tracer
        with _lf_session(
            "ycs-ingest-qdrant",
            session_id = self.request.id or "(no-request-id)",
        ):
            with get_tracer().start_as_current_span(
                "ycs.ingest.qdrant.run",
                attributes = {
                    "coelho.langfuse.keep": True,
                    "coelho.langfuse.kind": "workflow_root",
                    "langfuse.trace.name": "ycs.ingest.qdrant.run",
                    "langfuse.observation.metadata.workflow": "ycs_ingest",
                    "ycs.ingest.kind": "qdrant",
                    "ycs.chunk_size": int(chunk_size),
                    "ycs.chunk_overlap": int(chunk_overlap),
                    "ycs.video_count": len(video_ids or []),
                },
            ):
                set_current_span_langfuse_io(input_data = {
                    "kind": "qdrant",
                    "video_ids_preview": list(video_ids or [])[:10],
                    "video_count": len(video_ids or []),
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "task_id": self.request.id or "",
                })
                set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ingest",
                    "kind": "qdrant",
                    "task_id": self.request.id or "",
                    "video_count": len(video_ids or []),
                })
                set_current_span_langfuse_observation_metadata({
                    "kind": "qdrant",
                    "video_count": len(video_ids or []),
                })
                es = AsyncElasticsearch(
                    hosts      = [os.environ["ELASTICSEARCH_HOST"]],
                    basic_auth = (
                        os.environ["ELASTICSEARCH_USERNAME"],
                        os.environ.get("ELASTICSEARCH_PASSWORD", ""),
                    ),
                    verify_certs = False,
                )
                qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
                qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
                qdrant_api_key = os.environ.get("QDRANT_API_KEY")
                qdrant = AsyncQdrantClient(
                    url     = qdrant_url,
                    port    = qdrant_port,
                    api_key = qdrant_api_key if qdrant_api_key else None,
                )
                try:
                    try:
                        result = await run_ingestion(
                            es            = es,
                            qdrant        = qdrant,
                            video_ids     = video_ids,
                            chunk_size    = chunk_size,
                            chunk_overlap = chunk_overlap,
                            progress_cb   = _progress,
                        )
                    except Exception as e:
                        set_current_span_langfuse_io(output_data = {
                            "status": "failed",
                            "kind": "qdrant",
                            "task_id": self.request.id or "",
                            "error": f"{type(e).__name__}: {e}",
                        })
                        raise
                    set_current_span_langfuse_io(output_data = {
                        "status": "done",
                        "kind": "qdrant",
                        "task_id": self.request.id or "",
                        "result": result,
                    })
                    return result
                finally:
                    await qdrant.close()
                    await es.close()

    result = asyncio.run(_run())
    logger.info(f"[ingest_to_qdrant] Done: {result}")
    return result


@app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.stream_video_to_qdrant",
)
def stream_video_to_qdrant(
    self,
    video_id:      str,
    extract_id:    str,
    chunk_size:    int = 2000,
    chunk_overlap: int = 200,
) -> dict[str, Any]:
    """2026-09-13: per-video streaming counterpart to `ingest_to_qdrant`
    — dispatched once per video by `extract/task.py` as soon as that
    video's transcript lands in ES, instead of waiting for the whole
    batch. Chunks this ONE video and pushes onto `extract_id`'s shared
    Redis buffer (`ingestion/streaming.py`), flushing whenever the
    buffer crosses `FLUSH_CHUNKS`. Reports its outcome to
    `pipeline_task.streaming.mark_video_done`; whichever call turns out
    to be last for the run's Qdrant phase drains any buffer remainder
    and checks whether Neo4j's phase is also done to fire
    `invalidate_cache`."""
    logger.info(f"[stream_video_to_qdrant] {extract_id}: {video_id}")

    async def _run() -> dict[str, Any]:
        from domains.ycs.ingestion.streaming import (
            finalize_qdrant_buffer,
            stream_video_to_qdrant as _stream_one,
        )
        from domains.ycs.pipeline_task.streaming import (
            build_redis_client,
            mark_video_done,
            maybe_finalize,
            update_video_extra,
        )

        es = AsyncElasticsearch(
            hosts      = [os.environ["ELASTICSEARCH_HOST"]],
            basic_auth = (
                os.environ["ELASTICSEARCH_USERNAME"],
                os.environ.get("ELASTICSEARCH_PASSWORD", ""),
            ),
            verify_certs = False,
        )
        qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
        qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
        qdrant_api_key = os.environ.get("QDRANT_API_KEY")
        qdrant = AsyncQdrantClient(
            url     = qdrant_url,
            port    = qdrant_port,
            api_key = qdrant_api_key if qdrant_api_key else None,
        )
        redis = build_redis_client()
        try:
            result = await _stream_one(
                es = es, qdrant = qdrant, redis = redis,
                video_id = video_id, extract_id = extract_id,
                chunk_size = chunk_size, chunk_overlap = chunk_overlap,
            )
            success = "error" not in result
            # Summed across every video by `get_phase_progress` into
            # the aggregator's SUCCESS result — the numbers
            # `pipeline_panel.js`'s `_successHint("qdrant", ...)`
            # expects (`points_upserted`, `total_chunks`).
            extra = (
                {"error": result.get("error")} if not success
                else {
                    "points_upserted": result.get("points_flushed", 0),
                    "total_chunks":    result.get("chunks", 0),
                }
            )
            finished, total = await mark_video_done(
                redis, extract_id, "qdrant", video_id, success = success,
                extra = extra,
            )
            if total is not None and finished >= total:
                logger.info(
                    f"[stream_video_to_qdrant] {extract_id}: last video "
                    f"of the run's Qdrant phase ({finished}/{total}) — "
                    f"draining buffer remainder"
                )
                # 2026-09-14: `mark_video_done` for THIS video already
                # completed successfully above — a raise from the drain
                # itself must not fall into the `except` block below,
                # which would call `mark_video_done` a SECOND time for
                # this same video_id (double-incrementing the exactly-
                # once finished counter, corrupting it for the rest of
                # the run). `finalize_qdrant_buffer` already re-queues
                # on failure (see its docstring), so swallowing here
                # loses nothing — a later Rerun's drain picks it up.
                try:
                    drained = await finalize_qdrant_buffer(redis, qdrant, extract_id)
                    result["final_drain_points"] = drained
                    # 2026-09-14: the drain count must be patched onto
                    # THIS video's status entry (same `update_video_extra`
                    # pattern Neo4j uses for `entities_merged`) — it is
                    # only known AFTER `mark_video_done` already
                    # recorded this video's `points_upserted`, so
                    # without this the bar shows "0 points" on runs
                    # whose chunks all landed via the final drain (e.g.
                    # 43 chunks < FLUSH_CHUNKS=50).
                    if drained:
                        await update_video_extra(
                            redis, extract_id, "qdrant", video_id,
                            {"points_upserted": result.get("points_flushed", 0) + drained},
                        )
                except Exception as e:
                    logger.warning(
                        f"[stream_video_to_qdrant] {extract_id}: final "
                        f"drain failed (chunks re-queued for a later "
                        f"attempt): {type(e).__name__}: {e}"
                    )
            await maybe_finalize(redis, extract_id)
            return result
        except Exception as e:
            finished, total = await mark_video_done(
                redis, extract_id, "qdrant", video_id, success = False,
                extra = {"error": f"{type(e).__name__}: {e}"},
            )
            if total is not None and finished >= total:
                try:
                    await finalize_qdrant_buffer(redis, qdrant, extract_id)
                except Exception:
                    pass
            await maybe_finalize(redis, extract_id)
            raise
        finally:
            await qdrant.close()
            await es.close()
            await redis.close()

    result = asyncio.run(_run())
    logger.info(f"[stream_video_to_qdrant] {extract_id}: {video_id} done: {result}")
    return result


@app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.invalidate_cache",
)
def invalidate_cache(self) -> dict[str, Any]:
    """Clear all RAG search cache after new data ingestion.
"""
    async def _run() -> None:
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = os.environ.get("REDIS_PORT", "6379")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        url = (
            f"redis://:{redis_password}@{redis_host}:{redis_port}"
            if redis_password
            else f"redis://{redis_host}:{redis_port}"
        )
        r = redis_aio.from_url(url)
        try:
            await _invalidate_cache(r)
        finally:
            await r.close()
    asyncio.run(_run())
    return {"status": "cache_cleared"}
