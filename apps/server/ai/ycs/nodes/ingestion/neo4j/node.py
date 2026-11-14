"""Extract transcript entities and write the Neo4j knowledge graph."""
from __future__ import annotations

import domains
from ....graphs.ingestion import entities, states
from .. import support

import logging
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


async def extract_graph(
    state: states.IngestionChunkState,
    runtime: Runtime[entities.IngestionContext],
) -> dict[str, Any]:
    context = runtime.context
    extract_id = state.get("extract_id")
    video_ids = state.get("dispatched_ids", [])
    if not extract_id or not video_ids:
        return {"neo4j_result": {"processed_ids": [], "failed_ids": []}}

    batch_size = min(len(video_ids), domains.ycs.ingestion.graph_builder.params.EXTRACT_CONCURRENCY)
    async with context.neo4j_semaphore:
        if context.cancel_check and await context.cancel_check():
            return {"neo4j_result": {"processed_ids": [], "failed_ids": [], "cancelled_ids": video_ids}}
        try:
            result = await domains.ycs.ingestion.neo4j_task.service.ingest_async(
                video_ids,
                batch_size,
                True,
                extract_id,
                task_id = context.run_id or extract_id,
                progress_cb = lambda _payload: None,
            )
            return {"neo4j_result": result}
        except Exception as error:
            redis = domains.ycs.ingestion.pipeline_task.service.build_redis_client()
            part_totals = support.partition_totals(state.get("transcript_docs", []))
            try:
                for video_id in video_ids:
                    await support.mark_failed_if_unreported(
                        redis,
                        extract_id,
                        "neo4j",
                        video_id,
                        f"{type(error).__name__}: {error}",
                        part_totals.get(video_id),
                    )
            finally:
                await redis.close()
            logger.exception(f"[ycs:ingestion:graph] Neo4j node failed for {video_ids}")
            return {
                "neo4j_result": {
                    "processed_ids": [],
                    "failed_ids": list(video_ids),
                    "error": f"{type(error).__name__}: {error}",
                },
            }


__all__ = ["extract_graph"]
