"""ycs/es_index — async bulk-index helpers for metadata + transcriptions.
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


async def delete_videos_from_es(
    es:        AsyncElasticsearch,
    video_ids: list[str],
) -> dict[str, Any]:
    """Best-effort delete of the supplied video_ids from BOTH
    `INDEX_METADATA` and `INDEX_TRANSCRIPTIONS`. Used by the Pipeline
    panel's `Wipe cache` button so the next Retry re-fetches from yt-dlp
    + re-scrapes via Playwright instead of hitting the cache.

    Per-index query SHAPE differs by indexing convention:
      - `INDEX_METADATA`: video_id is the document `_id` (see
        `fetch_metadata_from_es` which uses `{ids: {values}}`). So
        deleting by `terms.video_id` matches nothing — the field
        doesn't exist in this index — and earlier this returned
        `metadata_deleted: 0` even when 5 metadata docs were present.
        Use `ids` query to target the `_id` field directly.
      - `INDEX_TRANSCRIPTIONS`: video_id is a regular field (one
        transcript doc per `{video_id}_{lang}` pair), so `terms`
        works.

    `delete_by_query` (vs. doc-by-doc DELETE) lets a single request
    drop every match — small/large batches behave the same. `conflicts:
    proceed` so a concurrent ingestion writer doesn't sink the wipe.
    Each index failure is logged + counted separately; the wipe
    proceeds across both even if one index errors."""
    if not video_ids:
        return {"metadata_deleted": 0, "transcripts_deleted": 0}
    out: dict[str, Any] = {}
    queries: tuple[tuple[str, str, dict], ...] = (
        ("metadata",    INDEX_METADATA,        {"ids":   {"values": list(video_ids)}}),
        ("transcripts", INDEX_TRANSCRIPTIONS,  {"terms": {"video_id": list(video_ids)}}),
    )
    for index_label, index_name, query in queries:
        try:
            resp = await es.delete_by_query(
                index = index_name,
                query = query,
                refresh = True,
                conflicts = "proceed",
            )
            n = int(resp.get("deleted", 0) or 0)
            out[f"{index_label}_deleted"] = n
            logger.info(
                f"[elasticsearch:wipe] {index_name}: deleted {n} doc(s)"
            )
        except Exception as e:
            logger.warning(
                f"[elasticsearch:wipe] {index_name} failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
            out[f"{index_label}_deleted"] = 0
            out[f"{index_label}_error"] = str(e)[:200]
    return out
