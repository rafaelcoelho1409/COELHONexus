"""ycs/es_index — async bulk-index helpers for metadata + transcriptions.

Direct port of deprecated `routers/v1/youtube/helpers.py:L1778-1859`.

Two near-symmetric writers (one per index). Both:
  - skip the no-op early
  - build `[{"index": {...}}, doc, ...]` ops
  - skip ops missing the doc id
  - emit `await es.bulk(operations=..., refresh=BULK_REFRESH)`
  - count items whose `item["index"]["status"] in INDEXED_STATUSES`
  - return `{indexed, failed, errors}` on success or
    `{indexed:0, failed:len(...), error:str(e)}` on exception

Refresh policy is `True` so callers (Celery tasks → Qdrant ingest, retriever
fetch) see new docs on the very next ES query."""
from __future__ import annotations

import logging
import time
from typing import Any

from elasticsearch import AsyncElasticsearch

from infra.elasticsearch import INDEX_METADATA, INDEX_TRANSCRIPTIONS

from .params import BULK_REFRESH, INDEXED_STATUSES


logger = logging.getLogger(__name__)


async def index_videos_to_elasticsearch(
    es_client: AsyncElasticsearch,
    videos: list[dict[str, Any]],
    index: str = INDEX_METADATA,
) -> dict[str, Any]:
    """Bulk-index video metadata. Each video's `id` field becomes the ES `_id`.

    Returns `{indexed, failed, errors}` on success. On exception, returns
    `{indexed: 0, failed: len(videos), error: str(e)}` — deprecated
    convention (callers tolerate either shape)."""
    if not videos:
        logger.info("[elasticsearch] skip indexing, no videos")
        return {"indexed": 0, "failed": 0}
    operations: list[dict[str, Any]] = []
    for video in videos:
        video_id = video.get("id")
        if not video_id:
            continue
        operations.append({"index": {"_index": index, "_id": video_id}})
        operations.append(video)
    if not operations:
        logger.info("[elasticsearch] skip indexing, no valid video IDs")
        return {"indexed": 0, "failed": 0}
    logger.info(
        f"[elasticsearch] indexing {len(operations) // 2} videos to {index}",
    )
    start_time = time.time()
    try:
        response = await es_client.bulk(
            operations = operations,
            refresh = BULK_REFRESH,
        )
        elapsed = time.time() - start_time
        indexed = sum(
            1
            for item in response["items"]
            if item["index"]["status"] in INDEXED_STATUSES
        )
        failed = len(response["items"]) - indexed
        logger.info(
            f"[elasticsearch] OK indexed={indexed} failed={failed} "
            f"time={elapsed:.2f}s",
        )
        return {
            "indexed": indexed,
            "failed":  failed,
            "errors":  response.get("errors", False),
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.info(
            f"[elasticsearch] ERROR time={elapsed:.2f}s "
            f"error={str(e)[:200]}",
        )
        return {"indexed": 0, "failed": len(videos), "error": str(e)}


async def index_transcriptions_to_elasticsearch(
    es_client: AsyncElasticsearch,
    transcriptions: list[dict[str, Any]],
    index: str = INDEX_TRANSCRIPTIONS,
) -> dict[str, Any]:
    """Bulk-index transcriptions. Each transcript's composite `id`
    (`{video_id}_{lang}`) becomes the ES `_id`.

    Same return-shape contract as `index_videos_to_elasticsearch`."""
    if not transcriptions:
        logger.info("[elasticsearch] skip indexing, no transcriptions")
        return {"indexed": 0, "failed": 0}
    operations: list[dict[str, Any]] = []
    for trans in transcriptions:
        doc_id = trans.get("id")
        if not doc_id:
            continue
        operations.append({"index": {"_index": index, "_id": doc_id}})
        operations.append(trans)
    if not operations:
        logger.info(
            "[elasticsearch] skip indexing, no valid transcription IDs",
        )
        return {"indexed": 0, "failed": 0}
    logger.info(
        f"[elasticsearch] indexing {len(operations) // 2} transcriptions "
        f"to {index}",
    )
    start_time = time.time()
    try:
        response = await es_client.bulk(
            operations = operations,
            refresh = BULK_REFRESH,
        )
        elapsed = time.time() - start_time
        indexed = sum(
            1
            for item in response["items"]
            if item["index"]["status"] in INDEXED_STATUSES
        )
        failed = len(response["items"]) - indexed
        logger.info(
            f"[elasticsearch] OK indexed={indexed} failed={failed} "
            f"time={elapsed:.2f}s",
        )
        return {
            "indexed": indexed,
            "failed":  failed,
            "errors":  response.get("errors", False),
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.info(
            f"[elasticsearch] ERROR time={elapsed:.2f}s "
            f"error={str(e)[:200]}",
        )
        return {
            "indexed": 0,
            "failed":  len(transcriptions),
            "error":   str(e),
        }
