"""ycs/ingestion/es_index — async bulk-index helpers for metadata + passages.
The writers:
  - skip the no-op early
  - build `[{"index": {...}}, doc, ...]` ops
  - skip ops missing the doc id
  - emit `await es.bulk(operations=..., refresh=params.BULK_REFRESH)`
  - count items whose `item["index"]["status"] in params.INDEXED_STATUSES`
  - return `{indexed, failed, errors}` on success or
    `{indexed:0, failed:len(...), error:str(e)}` on exception

Transcript passage documents use the same default chunker as Qdrant and are
written beside canonical transcripts in one refresh-waiting bulk request."""
from __future__ import annotations
import domains, infra
from . import params

import logging
import time
from typing import Any


logger = logging.getLogger(__name__)


async def index_videos_to_elasticsearch(
    es_client: infra.elasticsearch.service.AsyncElasticsearch,
    videos: list[dict[str, Any]],
    index: str = infra.elasticsearch.keys.INDEX_METADATA,
) -> dict[str, Any]:
    """Bulk-index video metadata. Each video's `id` field becomes the ES `_id`.

    Returns `{indexed, failed, errors}` on success. On exception, returns
    `{indexed: 0, failed: len(videos), error: str(e)}` — deprecated
    convention (callers tolerate either shape)."""
    if not videos:
        logger.info("[elasticsearch] skip indexing, no videos")
        return {"indexed": 0, "failed": 0}
    operations: list[dict[str, Any]] = []
    valid_videos: list[dict[str, Any]] = []
    for video in videos:
        video_id = video.get("id")
        if not video_id:
            continue
        valid_videos.append(video)
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
        with domains.ycs.ask.observability.service.es_search_span(
            index = index, top_k = len(operations) // 2, operation = "bulk_index",
        ):
            response = await es_client.bulk(
                operations = operations,
                refresh = params.BULK_REFRESH,
            )
        elapsed = time.time() - start_time
        indexed_ids: list[str] = []
        failed_ids: list[str] = []
        for video, item in zip(valid_videos, response.get("items", [])):
            video_id = str(video["id"])
            status = item.get("index", {}).get("status")
            (indexed_ids if status in params.INDEXED_STATUSES else failed_ids).append(video_id)
        accounted_ids = set(indexed_ids) | set(failed_ids)
        failed_ids.extend(
            str(video["id"])
            for video in valid_videos
            if str(video["id"]) not in accounted_ids
        )
        indexed = len(indexed_ids)
        failed = len(failed_ids)
        logger.info(
            f"[elasticsearch] indexed={indexed} failed={failed} "
            f"time={elapsed:.2f}s",
        )
        return {
            "indexed": indexed,
            "failed":  failed,
            "errors":  response.get("errors", False),
            "indexed_ids": indexed_ids,
            "failed_ids": failed_ids,
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.info(
            f"[elasticsearch] ERROR time={elapsed:.2f}s "
            f"error={str(e)[:200]}",
        )
        return {
            "indexed": 0,
            "failed": len(valid_videos),
            "failed_ids": [str(video["id"]) for video in valid_videos],
            "error": str(e),
        }


async def index_transcriptions_to_elasticsearch(
    es_client: infra.elasticsearch.service.AsyncElasticsearch,
    transcriptions: list[dict[str, Any]],
    index: str = infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
) -> dict[str, Any]:
    """Bulk-index transcriptions. Each transcript's composite `id`
    (`{video_id}_{lang}`) becomes the ES `_id`.

    Same return-shape contract as `index_videos_to_elasticsearch`."""
    if not transcriptions:
        logger.info("[elasticsearch] skip indexing, no transcriptions")
        return {"indexed": 0, "failed": 0}
    operations: list[dict[str, Any]] = []
    write_records: list[tuple[str, str]] = []
    valid_transcriptions: list[dict[str, Any]] = []
    status_by_transcript: dict[str, dict[str, Any]] = {}
    passage_index = infra.elasticsearch.keys.INDEX_YCS_RAG_PASSAGES
    chunker = domains.ycs.ingestion.chunker.domain.create_chunker()
    for trans in transcriptions:
        doc_id = trans.get("id")
        if not doc_id:
            continue
        valid_transcriptions.append(trans)
        video_id = str(trans.get("video_id") or "")
        passages = domains.ycs.ingestion.es_index.domain.transcript_passages(trans, chunker)
        status_by_transcript[str(doc_id)] = {
            "video_id": video_id,
            "canonical_ok": False,
            "passages_expected": len(passages),
            "passages_ok": 0,
            "failed": False,
        }
        operations.append({"index": {"_index": index, "_id": doc_id}})
        operations.append(trans)
        write_records.append((str(doc_id), "canonical"))
        for passage in passages:
            operations.append({"index": {"_index": passage_index, "_id": passage["_id"]}})
            operations.append(passage["_source"])
            write_records.append((str(doc_id), "passage"))
    if not operations:
        logger.info(
            "[elasticsearch] skip indexing, no valid transcription IDs",
        )
        return {"indexed": 0, "failed": 0}
    logger.info(
        f"[elasticsearch] indexing {len(valid_transcriptions)} transcriptions and "
        f"{len(write_records) - len(valid_transcriptions)} passages to {index} + {passage_index}",
    )
    start_time = time.time()
    try:
        transcript_ids = list(status_by_transcript)
        stale_passages_deleted = 0
        if transcript_ids and await es_client.indices.exists(index = passage_index):
            deleted = await es_client.delete_by_query(
                index = passage_index,
                query = {"terms": {"transcript_id": transcript_ids}},
                conflicts = "proceed",
                refresh = False,
            )
            if deleted.get("timed_out") or deleted.get("failures"):
                raise RuntimeError("Elasticsearch could not remove stale transcript passages")
            stale_passages_deleted = int(deleted.get("deleted", 0) or 0)
        with domains.ycs.ask.observability.service.es_search_span(
            index = index, top_k = len(valid_transcriptions), operation = "bulk_index",
        ):
            response = await es_client.bulk(
                operations = operations,
                refresh = params.BULK_REFRESH,
            )
        if stale_passages_deleted and not any(kind == "passage" for _, kind in write_records):
            await es_client.indices.refresh(index = passage_index)
        elapsed = time.time() - start_time
        response_items = response.get("items", [])
        for (transcript_id, kind), item in zip(write_records, response_items):
            status = item.get("index", {}).get("status")
            result = status_by_transcript[transcript_id]
            if status not in params.INDEXED_STATUSES:
                result["failed"] = True
            elif kind == "canonical":
                result["canonical_ok"] = True
            else:
                result["passages_ok"] += 1
        for transcript_id, _kind in write_records[len(response_items):]:
            status_by_transcript[transcript_id]["failed"] = True

        successful = {
            transcript_id for transcript_id, result in status_by_transcript.items()
            if result["canonical_ok"]
            and result["passages_ok"] == result["passages_expected"]
            and not result["failed"]
        }
        indexed_ids = list(dict.fromkeys(
            str(result["video_id"])
            for transcript_id, result in status_by_transcript.items()
            if transcript_id in successful and result["video_id"]
        ))
        failed_ids = list(dict.fromkeys(
            str(result["video_id"])
            for transcript_id, result in status_by_transcript.items()
            if transcript_id not in successful and result["video_id"]
        ))
        indexed = len(successful)
        failed = len(valid_transcriptions) - indexed
        passages_indexed = sum(result["passages_ok"] for result in status_by_transcript.values())
        errors = bool(response.get("errors", False) or failed)
        logger.info(
            f"[elasticsearch] OK indexed={indexed} failed={failed} "
            f"passages={passages_indexed} "
            f"time={elapsed:.2f}s",
        )
        return {
            "indexed": indexed,
            "failed":  failed,
            "passages_indexed": passages_indexed,
            "errors":  errors,
            "indexed_ids": list(dict.fromkeys(indexed_ids)),
            "failed_ids": list(dict.fromkeys(failed_ids)),
        }
    except Exception as e:
        elapsed = time.time() - start_time
        logger.info(
            f"[elasticsearch] ERROR time={elapsed:.2f}s "
            f"error={str(e)[:200]}",
        )
        return {
            "indexed": 0,
            "failed":  len(valid_transcriptions),
            "passages_indexed": 0,
            "failed_ids": list(dict.fromkeys(
                str(transcript["video_id"])
                for transcript in valid_transcriptions
                if transcript.get("video_id")
            )),
            "error":   str(e),
        }


async def delete_videos_from_es(
    es:        infra.elasticsearch.service.AsyncElasticsearch,
    video_ids: list[str],
) -> dict[str, Any]:
    """Best-effort delete of the supplied video_ids from metadata,
    canonical transcripts, and passage documents. Used by the Pipeline
    panel's `Wipe cache` button so the next Retry re-fetches from yt-dlp
    + re-scrapes via Playwright instead of hitting the cache.

    Per-index query SHAPE differs by indexing convention:
      - `INDEX_METADATA`: video_id is the document `_id` (see
        `fetch_metadata_from_es` which uses `{ids: {values}}`). So
        deleting by `terms.video_id` matches nothing — the field
        doesn't exist in this index — and earlier this returned
        `metadata_deleted: 0` even when 5 metadata docs were present.
        Use `ids` query to target the `_id` field directly. Metadata is
        never per-partition (only one doc per original video), so no
        `parent_video_id` half is needed here.
      - `INDEX_TRANSCRIPTIONS`: video_id is a regular field (one
        transcript doc per `{video_id}_{lang}` pair) — matched via
        `terms`. 2026-09-15: OR'd with `parent_video_id` too — a split
        video's transcript docs carry `video_id="XYZ#p{n}"`, never the
        parent id, so deleting "XYZ" alone previously left every
        partition's transcript doc behind.

    `delete_by_query` (vs. doc-by-doc DELETE) lets a single request
    drop every match — small/large batches behave the same. `conflicts:
    proceed` so a concurrent ingestion writer doesn't sink the wipe.
    Each index failure is logged + counted separately; the wipe proceeds
    across the canonical and passage indexes even if one index errors."""
    if not video_ids:
        return {"metadata_deleted": 0, "transcripts_deleted": 0, "passages_deleted": 0}
    out: dict[str, Any] = {}
    queries: tuple[tuple[str, str, dict], ...] = (
        ("metadata",    infra.elasticsearch.keys.INDEX_METADATA,        {"ids": {"values": list(video_ids)}}),
        ("transcripts", infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,  {"bool": {"should": [
            {"terms": {"video_id":                 list(video_ids)}},
            # The fresh mapping stores `parent_video_id` as a keyword.
            {"terms": {"parent_video_id": list(video_ids)}},
        ]}}),
        ("passages", infra.elasticsearch.keys.INDEX_YCS_RAG_PASSAGES, {"bool": {"should": [
            {"terms": {"video_id": list(video_ids)}},
            {"terms": {"parent_video_id": list(video_ids)}},
        ]}}),
    )
    for index_label, index_name, query in queries:
        try:
            with domains.ycs.ask.observability.service.es_search_span(
                index = index_name, top_k = len(video_ids), operation = "delete_by_query",
            ):
                resp = await es.delete_by_query(
                    index = index_name,
                    query = query,
                    refresh = False,
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
    indexes_to_refresh = [
        index_name for index_name in (
            infra.elasticsearch.keys.INDEX_METADATA,
            infra.elasticsearch.keys.INDEX_TRANSCRIPTIONS,
            infra.elasticsearch.keys.INDEX_YCS_RAG_PASSAGES,
        )
        if await es.indices.exists(index = index_name)
    ]
    if indexes_to_refresh:
        await es.indices.refresh(index = indexes_to_refresh)
    return out
