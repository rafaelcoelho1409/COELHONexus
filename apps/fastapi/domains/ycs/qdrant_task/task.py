"""ycs/qdrant_task — ES transcripts → chunk → embed → Qdrant upsert + cache invalidate."""
from __future__ import annotations
import infra.celery.service

import asyncio
import logging
import os
from typing import Any

import domains
import redis.asyncio as redis_aio
from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch
from qdrant_client import AsyncQdrantClient

logger = get_task_logger(__name__)


@infra.celery.service.app.task(
    bind = True,
    name = "domains.ycs.qdrant_task.task.ingest_to_qdrant",
)
def ingest_to_qdrant(
    self,
    video_ids:       list[str] | None = None,
    chunk_size:      int              = 2000,
    chunk_overlap:   int              = 200,
    collection_name: str | None       = None,
) -> dict[str, Any]:
    """Stream ES transcripts → chunk → embed → Qdrant upsert.

    `collection_name` (2026-09-15) — override for
    `domains.ycs.embedding_migration`'s re-embed job, which targets a
    fresh versioned staging collection instead of the live one; `None`
    (every other caller) keeps the default from `ingestion.service
    .ingest_to_qdrant`'s own signature (the live `QDRANT_COLLECTION`
    alias)."""
    logger.info(
        f"[ingest_to_qdrant] Starting: video_ids={video_ids}, "
        f"chunk_size={chunk_size}",
    )
    self.update_state(state = "PROGRESS", meta = {"phase": "init"})

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    async def _run() -> dict[str, Any]:
        import domains, infra
        with infra.langfuse.sessions.session(
            "ycs-ingest-qdrant",
            session_id = self.request.id or "(no-request-id)",
        ):
            with infra.otel.service.get_tracer().start_as_current_span(
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
                infra.langfuse.spans.set_current_span_langfuse_io(input_data = {
                    "kind": "qdrant",
                    "video_ids_preview": list(video_ids or [])[:10],
                    "video_count": len(video_ids or []),
                    "chunk_size": chunk_size,
                    "chunk_overlap": chunk_overlap,
                    "task_id": self.request.id or "",
                })
                infra.langfuse.spans.set_current_span_langfuse_trace_metadata({
                    "pipeline": "ycs_ingest",
                    "kind": "qdrant",
                    "task_id": self.request.id or "",
                    "video_count": len(video_ids or []),
                })
                infra.langfuse.spans.set_current_span_langfuse_observation_metadata({
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
                        _kwargs: dict[str, Any] = {
                            "es":            es,
                            "qdrant":        qdrant,
                            "video_ids":     video_ids,
                            "chunk_size":    chunk_size,
                            "chunk_overlap": chunk_overlap,
                            "progress_cb":   _progress,
                        }
                        if collection_name:
                            _kwargs["collection_name"] = collection_name
                        result = await domains.ycs.ingestion.service.ingest_to_qdrant(**_kwargs)
                    except Exception as e:
                        infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
                            "status": "failed",
                            "kind": "qdrant",
                            "task_id": self.request.id or "",
                            "error": f"{type(e).__name__}: {e}",
                        })
                        raise
                    infra.langfuse.spans.set_current_span_langfuse_io(output_data = {
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


@infra.celery.service.app.task(
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
    Redis buffer (`ingestion/service.py`), flushing whenever the
    buffer crosses `FLUSH_CHUNKS`. Reports its outcome to
    `pipeline_task.service.mark_video_done`; whichever call turns out
    to be last for the run's Qdrant phase drains any buffer remainder
    and checks whether Neo4j's phase is also done to fire
    `invalidate_cache`."""
    logger.info(f"[stream_video_to_qdrant] {extract_id}: {video_id}")

    async def _run() -> dict[str, Any]:
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
        redis = domains.ycs.pipeline_task.service.build_redis_client()
        try:
            result = await domains.ycs.ingestion.service.stream_video_to_qdrant(
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
            # 2026-09-14: long-video partitioning — derived from the id
            # string itself (robust even if the ES round-trip that
            # would normally carry `result["parent_video_id"]` failed),
            # `parent_vid == video_id` for every non-split video, which
            # makes `mark_video_or_partition_done` call straight through
            # to `mark_video_done` unchanged for the overwhelming
            # majority of calls.
            parent_vid = domains.ycs.ingestion.domain.parent_video_id(video_id)
            finished, total = await domains.ycs.pipeline_task.service.mark_video_or_partition_done(
                redis, extract_id, "qdrant", video_id, success = success,
                extra = extra,
                parent_video_id = parent_vid,
                part_total = result.get("part_total"),
            )
            # `finished is None` means this was one partition of a
            # still-incomplete group — nothing to do yet; the block
            # below only ever runs for a call that just completed
            # something at the phase-total level (a whole video,
            # split or not), never for a mid-group partition.
            if total is not None and finished is not None and finished >= total:
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
                    drained = await domains.ycs.ingestion.service.finalize_qdrant_buffer(redis, qdrant, extract_id)
                    result["final_drain_points"] = drained
                    # 2026-09-14: the drain count must be patched onto
                    # the DISPLAYED status entry — `parent_vid`, not
                    # `video_id`: a partition's own id never gets a
                    # `phase_status_key` entry of its own (its outcome
                    # lives in the partition-group hash instead, until
                    # the group completes and gets folded into one
                    # entry under the parent). Same `update_video_extra`
                    # pattern Neo4j uses for `entities_merged` — only
                    # known AFTER mark_video_done already recorded the
                    # video's `points_upserted`, so without this the
                    # bar shows "0 points" on runs whose chunks all
                    # landed via the final drain (e.g. 43 chunks <
                    # FLUSH_CHUNKS=50).
                    if drained:
                        await domains.ycs.pipeline_task.service.update_video_extra(
                            redis, extract_id, "qdrant", parent_vid,
                            {"points_upserted": result.get("points_flushed", 0) + drained},
                        )
                except Exception as e:
                    logger.warning(
                        f"[stream_video_to_qdrant] {extract_id}: final "
                        f"drain failed (chunks re-queued for a later "
                        f"attempt): {type(e).__name__}: {e}"
                    )
            await domains.ycs.pipeline_task.service.maybe_finalize(redis, extract_id)
            return result
        except Exception as e:
            # `finished`/`total` can only be learned by actually calling
            # mark_video_done (it owns the atomic counter) — so THIS
            # video is provisionally recorded failed first, same as
            # always. But if it turns out to be the run's last video
            # AND the recovery drain below succeeds, the failure was
            # transient (e.g. a cold-start embed_probe_async timeout)
            # and the data made it into Qdrant regardless — correcting
            # the record via update_video_extra (safe: merges fields,
            # never touches the finished counter) and skipping the
            # re-raise reflects what actually happened instead of
            # reporting a false failure for a run that fully succeeded.
            # `result` was never obtained (the exception happened
            # INSIDE `_stream_one`, before it could return one), so
            # `part_total` isn't available the way the success path
            # gets it — re-fetched here (cheap, single-doc) rather than
            # risking a partition-group premature-completion bug: if
            # `part_total` were silently treated as unknown/None,
            # `mark_video_or_partition_done`'s "wait for every sibling"
            # check is skipped entirely, and ONE partition's exception
            # would wrongly report the WHOLE video done after only 1 of
            # N partitions ever reported in.
            parent_vid = domains.ycs.ingestion.domain.parent_video_id(video_id)
            part_total = None
            if parent_vid != video_id:
                try:
                    _t = await domains.ycs.ingestion.service.fetch_transcripts_from_es(es, [video_id])
                    if _t and isinstance(_t[0], dict):
                        part_total = _t[0].get("part_total")
                except Exception:
                    pass
            finished, total = await domains.ycs.pipeline_task.service.mark_video_or_partition_done(
                redis, extract_id, "qdrant", video_id, success = False,
                extra = {"error": f"{type(e).__name__}: {e}"},
                parent_video_id = parent_vid,
                part_total = part_total,
            )
            if total is not None and finished is not None and finished >= total:
                try:
                    drained = await domains.ycs.ingestion.service.finalize_qdrant_buffer(redis, qdrant, extract_id)
                except Exception as drain_err:
                    drained = None
                    logger.warning(
                        f"[stream_video_to_qdrant] {extract_id}: final "
                        f"drain ALSO failed (chunks re-queued for a "
                        f"later attempt): {type(drain_err).__name__}: "
                        f"{drain_err}"
                    )
                if drained is not None:
                    logger.info(
                        f"[stream_video_to_qdrant] {extract_id}: {video_id} "
                        f"recovered via final drain ({drained} point(s)) "
                        f"after {type(e).__name__}: {e}"
                    )
                    await domains.ycs.pipeline_task.service.update_video_extra(
                        redis, extract_id, "qdrant", parent_vid,
                        {
                            "success":         True,
                            "error":           None,
                            "points_upserted": drained,
                            "recovered_from":  f"{type(e).__name__}: {str(e)[:200]}",
                        },
                    )
                    await domains.ycs.pipeline_task.service.maybe_finalize(redis, extract_id)
                    return {
                        "video_id":          video_id,
                        "chunks":            0,
                        "skipped":           False,
                        "points_flushed":    drained,
                        "recovered_from":    f"{type(e).__name__}: {str(e)[:200]}",
                    }
            await domains.ycs.pipeline_task.service.maybe_finalize(redis, extract_id)
            raise
        finally:
            await qdrant.close()
            await es.close()
            await redis.close()

    result = asyncio.run(_run())
    logger.info(f"[stream_video_to_qdrant] {extract_id}: {video_id} done: {result}")
    return result


@infra.celery.service.app.task(
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
            await domains.ycs.cache.service.invalidate_cache(r)
        finally:
            await r.close()
    asyncio.run(_run())
    return {"status": "cache_cleared"}
