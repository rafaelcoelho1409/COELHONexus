"""Persist fetched video metadata and transcripts before store fan-out."""
from __future__ import annotations

import domains
from ....graphs.ingestion import entities, states

import logging
from collections import defaultdict
from typing import Any

from langgraph.runtime import Runtime


logger = logging.getLogger(__name__)


async def index_documents(
    state: states.IngestionChunkState,
    runtime: Runtime[entities.IngestionContext],
) -> dict[str, Any]:
    context = runtime.context
    context.reporter.elasticsearch_started()
    metadata_videos = state.get("metadata_videos", [])
    transcript_docs = state.get("transcript_docs", [])
    cached_ids = state.get("cached_ids", [])
    metadata_result = await domains.ycs.ingestion.es_index.service.index_videos_to_elasticsearch(
        context.es_client,
        metadata_videos,
    )
    context.reporter.metadata_index_finished(
        [str(video_id) for video_id in metadata_result.get("indexed_ids", [])],
        [str(video_id) for video_id in metadata_result.get("failed_ids", [])],
    )
    if metadata_result.get("error") or metadata_result.get("failed", 0):
        logger.error(f"[ycs:ingestion:graph] metadata indexing partial failure: {metadata_result}")

    docs_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in transcript_docs:
        video_id = str(document.get("parent_video_id") or document.get("video_id") or "")
        if video_id:
            parent_id = domains.ycs.ingestion.domain.parent_video_id(video_id)
            docs_by_parent[parent_id].append(document)

    indexed_ids: list[str] = []
    index_failed_ids: list[str] = []
    indexed_transcripts = 0
    failed_transcripts = 0
    for video_id, documents in docs_by_parent.items():
        result = await domains.ycs.ingestion.es_index.service.index_transcriptions_to_elasticsearch(
            context.es_client,
            documents,
        )
        succeeded = (
            result.get("indexed") == len(documents)
            and result.get("failed", 0) == 0
            and not result.get("errors", False)
            and not result.get("error")
        )
        if succeeded:
            indexed_ids.extend(str(document["video_id"]) for document in documents if document.get("video_id"))
            indexed_transcripts += 1
            context.reporter.elasticsearch_finished(video_id, success = True)
        else:
            failed_transcripts += max(1, int(result.get("failed", 0) or 0))
            index_failed_ids.append(video_id)
            context.reporter.elasticsearch_finished(video_id, success = False)
            logger.error(f"[ycs:ingestion:graph] transcript indexing failed for {video_id}: {result}")

    dispatched_ids = list(dict.fromkeys([*cached_ids, *indexed_ids]))
    for video_id in cached_ids:
        context.reporter.elasticsearch_finished(video_id, success = True)
    context.reporter.register_dispatched(dispatched_ids, transcript_docs)

    transcription_result = {
        "indexed": indexed_transcripts,
        "failed": failed_transcripts,
        "cached": len(cached_ids),
    }
    result = {"metadata": metadata_result, "transcriptions": transcription_result}
    return {
        "metadata_result": metadata_result,
        "transcription_result": transcription_result,
        "elasticsearch_result": result,
        "dispatched_ids": dispatched_ids,
        "index_failed_ids": index_failed_ids,
    }


def dispatch_to_stores(
    state: states.IngestionChunkState,
) -> str | list[str]:
    video_ids = state.get("dispatched_ids", [])
    extract_id = state.get("extract_id")
    if not video_ids or not extract_id:
        return "done"
    return ["qdrant", "neo4j"]


__all__ = ["dispatch_to_stores", "index_documents"]
