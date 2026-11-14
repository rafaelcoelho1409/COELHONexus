"""Shared outcome handling for ingestion store nodes."""
from __future__ import annotations

import domains

from typing import Any


async def mark_failed_if_unreported(
    redis: Any,
    extract_id: str,
    phase: str,
    video_id: str,
    error: str,
    part_total: int | None = None,
) -> None:
    parent_video_id = domains.ycs.ingestion.domain.parent_video_id(video_id)
    if parent_video_id == video_id:
        existing = await redis.hget(
            domains.ycs.ingestion.pipeline_task.keys.phase_status_key(extract_id, phase),
            parent_video_id,
        )
    else:
        existing = await redis.hget(
            domains.ycs.ingestion.pipeline_task.keys.partition_group_key(
                extract_id,
                phase,
                parent_video_id,
            ),
            video_id,
        )
        parent_status = await redis.hget(
            domains.ycs.ingestion.pipeline_task.keys.phase_status_key(extract_id, phase),
            parent_video_id,
        )
        if parent_status:
            return
    if existing:
        return
    await domains.ycs.ingestion.pipeline_task.service.mark_video_or_partition_done(
        redis,
        extract_id,
        phase,
        video_id,
        success = False,
        extra = {"error": error},
        parent_video_id = parent_video_id,
        part_total = part_total,
    )
    await domains.ycs.ingestion.pipeline_task.service.maybe_finalize(redis, extract_id)


def partition_totals(transcript_docs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        str(document["video_id"]): int(document["part_total"])
        for document in transcript_docs
        if document.get("video_id") and document.get("part_total")
    }


__all__ = ["mark_failed_if_unreported", "partition_totals"]
