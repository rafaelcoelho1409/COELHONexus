"""Chunk and embed indexed transcript documents into Qdrant."""
from __future__ import annotations

import domains
import domains.ycs.ingestion.qdrant_task.service
from ....graphs.ingestion import entities, states
from .. import support

import asyncio
import logging
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


async def index_vectors(
    state: states.IngestionChunkState,
    runtime: Runtime[entities.IngestionContext],
) -> dict[str, Any]:
    context = runtime.context
    extract_id = state.get("extract_id")
    video_ids = state.get("dispatched_ids", [])
    if not extract_id or not video_ids:
        return {"qdrant_result": {"processed_ids": [], "failed_ids": []}}

    redis = domains.ycs.ingestion.pipeline_task.service.build_redis_client()
    part_totals = support.partition_totals(state.get("transcript_docs", []))

    async def process(video_id: str) -> dict[str, Any]:
        async with context.qdrant_semaphore:
            if context.cancel_check and await context.cancel_check():
                return {"video_id": video_id, "cancelled": True}
            try:
                result = await domains.ycs.ingestion.qdrant_task.service.stream_video_to_qdrant_async(
                    video_id,
                    extract_id,
                )
                if result.get("error"):
                    return {
                        "video_id": video_id,
                        "result": result,
                        "error": str(result["error"]),
                    }
                return {"video_id": video_id, "result": result}
            except Exception as error:
                await support.mark_failed_if_unreported(
                    redis,
                    extract_id,
                    "qdrant",
                    video_id,
                    f"{type(error).__name__}: {error}",
                    part_totals.get(video_id),
                )
                logger.exception(f"[ycs:ingestion:graph] Qdrant node failed for {video_id}")
                return {"video_id": video_id, "error": f"{type(error).__name__}: {error}"}

    try:
        outcomes = await asyncio.gather(*(process(video_id) for video_id in video_ids))
    finally:
        await redis.close()
    return {
        "qdrant_result": {
            "processed_ids": [
                outcome["video_id"]
                for outcome in outcomes
                if "result" in outcome and "error" not in outcome
            ],
            "failed_ids": [outcome["video_id"] for outcome in outcomes if "error" in outcome],
            "cancelled_ids": [outcome["video_id"] for outcome in outcomes if outcome.get("cancelled")],
            "outcomes": outcomes,
        },
    }


__all__ = ["index_vectors"]
