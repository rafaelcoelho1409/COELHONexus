"""ycs/extract — Celery tasks: yt-dlp metadata + Playwright transcripts → ES.
Three tasks
(one per ingestion mode: by video IDs, by channel, by playlist). Each:
  1. opens a fresh `AsyncElasticsearch` for the worker process
  2. dispatches `YtDlpExtractor.extract_{batch,channel,playlist}`
  3. bulk-indexes metadata via `domains.ycs.es_index`
  4. (if `include_transcription`) initializes Playwright service,
     runs `fetch_transcriptions_batch`, and bulk-indexes transcripts
  5. always closes ES (and the transcript service when used)

Celery is sync; async work is wrapped in `asyncio.run(...)`. The
`@app.task(bind=True)` decorator gives access to `self.update_state(...)`
for progress reporting, which Flower and `GET /tasks/{id}` consume.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch

from domains.ycs.es_index import (
    index_transcriptions_to_elasticsearch,
    index_videos_to_elasticsearch,
)
from domains.ycs.transcript import (
    MAX_CONCURRENT,
    close_transcript_service,
    fetch_transcriptions_batch,
    init_transcript_service,
)
from infra.celery import app

from .service import get_extractor


# Callback signature for live progress emission. The task wrapper
# supplies a closure that pipes payloads into `self.update_state(meta=)`;
# the async impl passes per-stage dicts so the FastHTML poller can show
# the current video metadata + phase counters.
ProgressCb = Callable[[dict[str, Any]], None]


def _project_video_meta(v: dict[str, Any]) -> dict[str, Any]:
    """Pluck the subset of yt-dlp metadata shown on the FastHTML
    progress card — same shape as the Search-page result row (minus
    the thumbnail). Centralized so the 3 extract paths emit identical
    payloads."""
    return {
        "id":              v.get("id"),
        "title":           v.get("title"),
        "channel":         v.get("channel"),
        "channel_id":      v.get("channel_id"),
        "duration":        v.get("duration"),
        "duration_string": v.get("duration_string"),
        "view_count":      v.get("view_count"),
        "like_count":      v.get("like_count"),
        "upload_date":     v.get("upload_date"),
        "webpage_url":     v.get("webpage_url"),
    }


logger = get_task_logger(__name__)


# Fresh client factory (Celery worker process)
def _get_es_client() -> AsyncElasticsearch:
    """Build a fresh ES client owned by the running Celery task. The infra
    `get_es()` singleton lives in the FastAPI process; the Celery worker
    is a separate process and needs its own connection pool — deprecated
    pattern (`tasks/youtube/crawler.py:L27-38`)."""
    return AsyncElasticsearch(
        hosts      = [os.environ["ELASTICSEARCH_HOST"]],
        basic_auth = (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ.get("ELASTICSEARCH_PASSWORD", ""),
        ),
        verify_certs = False,
    )


async def _dispatch_streaming_totals(
    extract_id: str, dispatched_count: int,
) -> None:
    """Called once, after `_extract_videos_async`'s fetch loop finishes
    dispatching every video's downstream work. Records the Neo4j/Qdrant
    phase totals so `pipeline_task.streaming.maybe_finalize` knows when
    the run is actually done, then makes one speculative finalize check
    itself — covering the (unlikely but real) race where every
    dispatched per-video task already finished before this function got
    a chance to set the totals."""
    from domains.ycs.pipeline_task.streaming import (
        build_redis_client,
        maybe_finalize,
        set_phase_total,
    )
    if dispatched_count == 0:
        # Nothing fetched this run (all cached-in-ES-already videos
        # notwithstanding — see below) — nothing for Neo4j/Qdrant to
        # do, so there's nothing to wait on. Fire the cache-bust
        # directly; a run with zero new content still touched ES
        # metadata, so this stays cheap/harmless either way.
        from domains.ycs.qdrant_task.task import invalidate_cache
        invalidate_cache.delay()
        return
    redis = build_redis_client()
    try:
        await set_phase_total(redis, extract_id, "neo4j", dispatched_count)
        await set_phase_total(redis, extract_id, "qdrant", dispatched_count)
        await maybe_finalize(redis, extract_id)
    finally:
        await redis.close()


# Async implementations (called via asyncio.run from the Celery tasks)
async def _extract_videos_async(
    video_ids:             list[str],
    include_transcription: bool,
    languages:             list[str] | None,
    progress_cb:           ProgressCb | None = None,
    extract_id:            str | None        = None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = get_extractor()
    try:
        if progress_cb:
            progress_cb({
                "phase":   "metadata",
                "current": 0,
                "total":   len(video_ids),
            })
        videos = await extractor.extract_batch(video_ids)
        videos_dicts = [
            v.model_dump(exclude_none = False) if hasattr(v, "model_dump") else v
            for v in videos
        ]
        es_metadata = await index_videos_to_elasticsearch(es, videos_dicts)
        # transcript progress callback uses it to surface the most
        # recently completed video to the UI.
        videos_meta_map: dict[str, dict[str, Any]] = {
            v["id"]: _project_video_meta(v)
            for v in videos_dicts if v.get("id")
        }
        # column video list can render with titles + channels
        # immediately — before any transcript fetch finishes. Otherwise
        # the user would stare at video_ids until Phase 1 completes,
        # then suddenly see full metadata. We also stash `all_items`
        # in a closure variable + replay it on every subsequent
        # transcription progress payload (Celery `update_state(meta=)`
        # REPLACES the dict each call — a one-shot emit would be
        # overwritten before the JS poll lands on it).
        all_items = [
            videos_meta_map[vid] for vid in video_ids
            if vid in videos_meta_map
        ]
        if progress_cb:
            progress_cb({
                "phase":     "metadata_done",
                "current":   0,
                "total":     len(video_ids),
                "all_items": all_items,
            })
        es_transcriptions = {"indexed": 0, "failed": 0}
        dispatched_count = 0
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            video_metadata = {
                v["id"]: {
                    "channel_id":  v.get("channel_id"),
                    "playlist_id": v.get("playlist_id"),
                }
                for v in videos_dicts if v.get("id")
            }

            # 2026-09-14: Neo4j no longer gets one Celery task PER VIDEO.
            # Live-tested on a 25-video Capital Global batch: with only
            # 2 total Celery worker slots (shared across every domain)
            # and `extract_videos` itself pinning one slot for its whole
            # run, per-video dispatch left exactly 1 slot for ALL
            # downstream work — 21 videos ran through Neo4j strictly
            # one at a time, throwing away `extract_and_store_graph`'s
            # own internal `EXTRACT_CONCURRENCY`-wide (5) asyncio pool
            # entirely (a pool of width 5 processing 1 document at a
            # time buys nothing). That's a real regression from the
            # PRE-streaming design, where one `ingest_to_neo4j` call
            # processed 5 videos concurrently.
            #
            # Fix: accumulate video ids into a chunk here (in-process —
            # `extract_videos` is the one place already watching every
            # video complete, in order, so no Redis queue is needed for
            # this) and flush ONE `ingest_to_neo4j` call per chunk, with
            # `batch_size=len(chunk)` so the internal pool width exactly
            # matches the chunk — full concurrency restored, while still
            # starting well before the whole 25-video batch finishes
            # Phase 1 (chunks flush every `EXTRACT_CONCURRENCY` videos,
            # not after all 25). `ingest_to_neo4j` already natively
            # accepts and pools a list — this reverts ONLY the call
            # site back to batching, not the task's own logic.
            #
            # Qdrant stays per-video: its tasks complete in ~0.1-0.5s
            # each (chunk + buffer-push, occasionally a flush) — never
            # the bottleneck, no reason to add chunking complexity there.
            neo4j_chunk: list[str] = []
            _pending_track_tasks: list[asyncio.Task] = []

            def _flush_neo4j_chunk() -> None:
                nonlocal neo4j_chunk
                if not neo4j_chunk:
                    return
                from domains.ycs.neo4j_task.task import ingest_to_neo4j
                chunk = neo4j_chunk
                neo4j_chunk = []
                neo4j_task = ingest_to_neo4j.si(
                    chunk, len(chunk), skip_resolution = True, extract_id = extract_id,
                ).apply_async()
                _track_dispatched(neo4j_task.id)

            def _track_dispatched(task_id: str) -> None:
                # Gathered before this function returns (see the
                # `asyncio.gather` below) instead of pure fire-and-
                # forget — the previous fire-and-forget version showed
                # up live as repeated "Task was destroyed but it is
                # pending" warnings (the event loop tore down before
                # some of these ever got to run), meaning Stop's
                # revoke list was silently incomplete.
                from domains.ycs.pipeline_task.streaming import (
                    build_redis_client, track_dispatched_task,
                )
                async def _track() -> None:
                    r = build_redis_client()
                    try:
                        await track_dispatched_task(r, extract_id, task_id)
                    finally:
                        await r.close()
                _pending_track_tasks.append(asyncio.ensure_future(_track()))

            def _on_video_indexed(vid: str) -> None:
                # 2026-09-13: fires the instant a video's transcript is
                # safely committed to ES (freshly fetched, OR already
                # cached from a prior run) — dispatches that video's
                # Neo4j + Qdrant streaming work immediately, instead of
                # waiting for the whole batch to clear Phase 1 first.
                # `extract_id` is this task's own id (`self.request.id`,
                # threaded in from `extract_videos`) — the namespace
                # every downstream piece of Redis bookkeeping shares.
                nonlocal dispatched_count
                from domains.ycs.graph_builder.params import EXTRACT_CONCURRENCY
                from domains.ycs.qdrant_task.task import stream_video_to_qdrant
                dispatched_count += 1
                neo4j_chunk.append(vid)
                if len(neo4j_chunk) >= EXTRACT_CONCURRENCY:
                    _flush_neo4j_chunk()
                qdrant_task = stream_video_to_qdrant.si(
                    vid, extract_id,
                ).apply_async()
                _track_dispatched(qdrant_task.id)
            # Per-video status tracking for the Ingest-page video list.
            # The right-column status pill is derived from these lists:
            #   id in failed_ids                → Failed
            #   id == current_item.id (active)  → Processing
            #   id in completed_ids             → Done
            #   else                            → Queued
            completed_ids: list[str] = []
            failed_ids:    list[str] = []
            if progress_cb:
                progress_cb({
                    "phase":         "transcription",
                    "current":       0,
                    "total":         len(valid_ids),
                    "completed_ids": list(completed_ids),
                    "failed_ids":    list(failed_ids),
                    "all_items":     all_items,
                })

            def _per_video_cb(
                done: int, total: int, video_id: str | None,
                success: bool = True,
            ) -> None:
                if not progress_cb:
                    return
                if video_id:
                    if success and video_id not in completed_ids:
                        completed_ids.append(video_id)
                    elif (not success) and video_id not in failed_ids:
                        failed_ids.append(video_id)
                payload: dict[str, Any] = {
                    "phase":         "transcription",
                    "current":       done,
                    "total":         total,
                    "completed_ids": list(completed_ids),
                    "failed_ids":    list(failed_ids),
                    "all_items":     all_items,
                }
                if video_id and video_id in videos_meta_map:
                    payload["current_item"] = videos_meta_map[video_id]
                progress_cb(payload)

            transcript_service = await init_transcript_service(
                max_concurrent           = MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            def _es_index_cb(indexed: int, total: int) -> None:
                # Phase 2 (ElasticSearch) — separate bar from Phase 1
                # (Playwright). Chunk-grained by nature (one bulk write
                # per chunk), so this ticks in jumps, not smoothly.
                #
                # Carries completed_ids/failed_ids/all_items forward from
                # the transcription phase — Celery's update_state REPLACES
                # the meta dict each call, so without this the drawer
                # table's per-video ES column would go blank the instant
                # this phase starts (its own payload has no per-video
                # detail, only phase/current/total).
                if not progress_cb:
                    return
                try:
                    progress_cb({
                        "phase":         "es_indexing",
                        "current":       indexed,
                        "total":         total,
                        "completed_ids": list(completed_ids),
                        "failed_ids":    list(failed_ids),
                        "all_items":     all_items,
                    })
                except Exception as cb_err:
                    logger.warning(
                        f"[extract_videos] es_index progress_cb raised: "
                        f"{type(cb_err).__name__}: {cb_err}"
                    )
            try:
                trans_stats: dict[str, int] = {}
                await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    progress_cb        = _per_video_cb if progress_cb else None,
                    es_progress_cb     = _es_index_cb if progress_cb else None,
                    on_video_indexed   = _on_video_indexed if extract_id else None,
                    stats              = trans_stats,
                )
                # 2026-09-13: ES indexing now happens PER-VIDEO inside
                # `fetch_transcriptions_batch` itself (each doc is
                # written the instant its video's fetch succeeds, so
                # `_on_video_indexed` can safely trigger downstream
                # Neo4j/Qdrant work against a document guaranteed to
                # already be searchable — `BULK_REFRESH=True` makes the
                # per-video write block until visible). The bulk
                # re-index that used to happen here on the full
                # `transcription_docs` list would just be re-writing
                # the exact same docs a second time — removed.
                es_transcriptions["indexed"]       = trans_stats.get("fetched_ok", 0)
                es_transcriptions["failed"]        = trans_stats.get("index_write_failed", 0)
                # Augment ES indexing counters with cache + fetch
                # breakdown so the Ingest hint can show
                # "N cached · M new · K failed".
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
                # Flush whatever's left below the EXTRACT_CONCURRENCY
                # threshold — otherwise a tail of 1-4 videos would never
                # get a Neo4j task at all.
                _flush_neo4j_chunk()
                if _pending_track_tasks:
                    await asyncio.gather(*_pending_track_tasks, return_exceptions = True)
            finally:
                await close_transcript_service()
        if extract_id:
            await _dispatch_streaming_totals(extract_id, dispatched_count)
        return {
            "total_videos":   len(videos_dicts),
            "metadata":       es_metadata,
            "transcriptions": es_transcriptions,
            # Surface in the final result too, so a JS poll that lands
            # only AFTER Phase 1 reaches SUCCESS (page reload mid-Phase-2/3,
            # late-tab visit) still gets titles to render the right
            # column with names instead of bare video_ids.
            "all_items":      all_items,
        }
    finally:
        await es.close()


async def _extract_channel_async(
    channel_id:            str,
    max_results:           int,
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = get_extractor()
    try:
        result = await extractor.extract_channel(channel_id, max_results)
        # ChannelResult schema → dict
        result_dict = (
            result.model_dump(exclude_none = False)
            if hasattr(result, "model_dump")
            else result
        )
        videos = result_dict.get("videos", [])
        videos_dicts = [
            v if isinstance(v, dict) else v.model_dump(exclude_none = False)
            for v in videos
        ]
        es_metadata = await index_videos_to_elasticsearch(es, videos_dicts)
        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            channel_id_val = result_dict.get("channel_id")
            video_metadata = {
                v["id"]: {
                    "channel_id":  channel_id_val,
                    "playlist_id": v.get("playlist_id"),
                }
                for v in videos_dicts if v.get("id")
            }
            transcript_service = await init_transcript_service(
                max_concurrent           = MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                trans_stats: dict[str, int] = {}
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    stats              = trans_stats,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
            finally:
                await close_transcript_service()
        return {
            "channel_id":     result_dict.get("channel_id"),
            "channel_name":   result_dict.get("channel_title"),
            "total_videos":   len(videos_dicts),
            "metadata":       es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


async def _extract_playlist_async(
    playlist_id:           str,
    max_results:           int,
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    es = _get_es_client()
    extractor = get_extractor()
    try:
        result = await extractor.extract_playlist(playlist_id, max_results)
        result_dict = (
            result.model_dump(exclude_none = False)
            if hasattr(result, "model_dump")
            else result
        )
        videos = result_dict.get("videos", [])
        videos_dicts = [
            v if isinstance(v, dict) else v.model_dump(exclude_none = False)
            for v in videos
        ]
        es_metadata = await index_videos_to_elasticsearch(es, videos_dicts)
        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [
                v["id"] for v in videos_dicts
                if v.get("id") and "error" not in v
            ]
            playlist_id_val = result_dict.get("playlist_id")
            video_metadata = {
                v["id"]: {
                    "channel_id":  v.get("channel_id"),
                    "playlist_id": playlist_id_val,
                }
                for v in videos_dicts if v.get("id")
            }
            transcript_service = await init_transcript_service(
                max_concurrent           = MAX_CONCURRENT,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                trans_stats: dict[str, int] = {}
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                    stats              = trans_stats,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
                es_transcriptions["cached"]       = trans_stats.get("cached", 0)
                es_transcriptions["fetch_failed"] = trans_stats.get("fetched_failed", 0)
                es_transcriptions["no_transcript"] = trans_stats.get("no_transcript", 0)
            finally:
                await close_transcript_service()
        return {
            "playlist_id":     result_dict.get("playlist_id"),
            "playlist_title":  result_dict.get("playlist_title"),
            "total_videos":    len(videos_dicts),
            "metadata":        es_metadata,
            "transcriptions":  es_transcriptions,
        }
    finally:
        await es.close()


# Celery tasks (sync wrappers — Celery is sync by default)
@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_videos",
)
def extract_videos(
    self,
    video_ids:             list[str],
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract metadata + transcripts for specific video IDs → ES."""
    logger.info(f"[extract_videos] Starting: {len(video_ids)} videos")
    self.update_state(
        state = "PROGRESS",
        meta  = {"phase": "init", "total": len(video_ids)},
    )

    def _progress(payload: dict[str, Any]) -> None:
        self.update_state(state = "PROGRESS", meta = payload)

    result = asyncio.run(
        _extract_videos_async(
            video_ids, include_transcription, languages,
            progress_cb = _progress,
            extract_id  = self.request.id,
        ),
    )
    logger.info(f"[extract_videos] Done: {result}")
    return result


@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_channel",
)
def extract_channel(
    self,
    channel_id:            str,
    max_results:           int              = 0,
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract all channel videos → ES (max_results=0 = all)."""
    logger.info(
        f"[extract_channel] Starting: {channel_id} (max={max_results})",
    )
    self.update_state(
        state = "PROGRESS",
        meta  = {"status": "extracting", "channel_id": channel_id},
    )
    result = asyncio.run(
        _extract_channel_async(
            channel_id, max_results, include_transcription, languages,
        ),
    )
    logger.info(
        f"[extract_channel] Done: {result.get('total_videos')} videos",
    )
    return result


@app.task(
    bind = True,
    name = "domains.ycs.extract.task.extract_playlist",
)
def extract_playlist(
    self,
    playlist_id:           str,
    max_results:           int              = 0,
    include_transcription: bool             = True,
    languages:             list[str] | None = None,
) -> dict[str, Any]:
    """Extract all playlist videos → ES (max_results=0 = all)."""
    logger.info(
        f"[extract_playlist] Starting: {playlist_id} (max={max_results})",
    )
    self.update_state(
        state = "PROGRESS",
        meta  = {"status": "extracting", "playlist_id": playlist_id},
    )
    result = asyncio.run(
        _extract_playlist_async(
            playlist_id, max_results, include_transcription, languages,
        ),
    )
    logger.info(
        f"[extract_playlist] Done: {result.get('total_videos')} videos",
    )
    return result
