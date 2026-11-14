"""Async workers for YCS's streaming Qdrant ingestion stage."""
from __future__ import annotations

import domains, infra

import logging
from typing import Any


logger = logging.getLogger(__name__)


async def stream_video_to_qdrant_async(
    video_id: str,
    extract_id: str,
    chunk_size: int = 2000,
    chunk_overlap: int = 200,
) -> dict[str, Any]:
    """Chunk one indexed transcript, write Qdrant, and report its outcome."""
    es = infra.elasticsearch.service.new_es()
    qdrant = infra.qdrant.service.new_qdrant()
    redis = domains.ycs.ingestion.pipeline_task.service.build_redis_client()
    try:
        try:
            result = await domains.ycs.ingestion.service.stream_video_to_qdrant(
                es = es,
                qdrant = qdrant,
                redis = redis,
                video_id = video_id,
                extract_id = extract_id,
                chunk_size = chunk_size,
                chunk_overlap = chunk_overlap,
            )
            success = "error" not in result
            extra = (
                {"error": result.get("error")}
                if not success
                else {
                    "points_upserted": result.get("points_flushed", 0),
                    "total_chunks": result.get("chunks", 0),
                }
            )
            parent_video_id = domains.ycs.ingestion.domain.parent_video_id(video_id)
            finished, total = await domains.ycs.ingestion.pipeline_task.service.mark_video_or_partition_done(
                redis,
                extract_id,
                "qdrant",
                video_id,
                success = success,
                extra = extra,
                parent_video_id = parent_video_id,
                part_total = result.get("part_total"),
            )
            if total is not None and finished is not None and finished >= total:
                try:
                    drained = await domains.ycs.ingestion.service.finalize_qdrant_buffer(
                        redis,
                        qdrant,
                        extract_id,
                    )
                    result["final_drain_points"] = drained
                    if drained:
                        await domains.ycs.ingestion.pipeline_task.service.update_video_extra(
                            redis,
                            extract_id,
                            "qdrant",
                            parent_video_id,
                            {"points_upserted": result.get("points_flushed", 0) + drained},
                        )
                except Exception as error:
                    logger.warning(
                        f"[stream_video_to_qdrant] {extract_id}: final drain failed "
                        f"(chunks re-queued): {type(error).__name__}: {error}"
                    )
            await domains.ycs.ingestion.pipeline_task.service.maybe_finalize(redis, extract_id)
            return result
        except Exception as error:
            parent_video_id = domains.ycs.ingestion.domain.parent_video_id(video_id)
            part_total = None
            if parent_video_id != video_id:
                try:
                    transcripts = await domains.ycs.ingestion.service.fetch_transcripts_from_es(es, [video_id])
                    if transcripts and isinstance(transcripts[0], dict):
                        part_total = transcripts[0].get("part_total")
                except Exception:
                    pass
            finished, total = await domains.ycs.ingestion.pipeline_task.service.mark_video_or_partition_done(
                redis,
                extract_id,
                "qdrant",
                video_id,
                success = False,
                extra = {"error": f"{type(error).__name__}: {error}"},
                parent_video_id = parent_video_id,
                part_total = part_total,
            )
            if total is not None and finished is not None and finished >= total:
                try:
                    drained = await domains.ycs.ingestion.service.finalize_qdrant_buffer(
                        redis,
                        qdrant,
                        extract_id,
                    )
                except Exception as drain_error:
                    drained = None
                    logger.warning(
                        f"[stream_video_to_qdrant] {extract_id}: final drain also failed "
                        f"(chunks re-queued): {type(drain_error).__name__}: {drain_error}"
                    )
                if drained is not None:
                    await domains.ycs.ingestion.pipeline_task.service.update_video_extra(
                        redis,
                        extract_id,
                        "qdrant",
                        parent_video_id,
                        {
                            "success": True,
                            "error": None,
                            "points_upserted": drained,
                            "recovered_from": f"{type(error).__name__}: {str(error)[:200]}",
                        },
                    )
                    await domains.ycs.ingestion.pipeline_task.service.maybe_finalize(redis, extract_id)
                    return {
                        "video_id": video_id,
                        "chunks": 0,
                        "skipped": False,
                        "points_flushed": drained,
                        "recovered_from": f"{type(error).__name__}: {str(error)[:200]}",
                    }
            await domains.ycs.ingestion.pipeline_task.service.maybe_finalize(redis, extract_id)
            raise
    finally:
        await qdrant.close()
        await es.close()
        await redis.close()
