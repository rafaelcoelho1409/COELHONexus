"""ycs/extract — Celery tasks: yt-dlp metadata + Playwright transcripts → ES.

Direct port of deprecated `tasks/youtube/crawler.py:L41-292`. Three tasks
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
from typing import Any

from celery.utils.log import get_task_logger
from elasticsearch import AsyncElasticsearch

from domains.ycs.es_index import (
    index_transcriptions_to_elasticsearch,
    index_videos_to_elasticsearch,
)
from domains.ycs.transcript import (
    close_transcript_service,
    fetch_transcriptions_batch,
    init_transcript_service,
)
from infra.celery import app

from .service import get_extractor


logger = get_task_logger(__name__)


# =============================================================================
# Fresh client factory (Celery worker process)
# =============================================================================
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


# =============================================================================
# Async implementations (called via asyncio.run from the Celery tasks)
# =============================================================================
async def _extract_videos_async(
    video_ids:             list[str],
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    """Port of deprecated `_extract_videos_async` (crawler.py:L41-95)."""
    es = _get_es_client()
    extractor = get_extractor()
    try:
        videos = await extractor.extract_batch(video_ids)
        videos_dicts = [
            v.model_dump(exclude_none = False) if hasattr(v, "model_dump") else v
            for v in videos
        ]
        es_metadata = await index_videos_to_elasticsearch(es, videos_dicts)
        es_transcriptions = {"indexed": 0, "failed": 0}
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
            transcript_service = await init_transcript_service(
                max_concurrent           = 5,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
            finally:
                await close_transcript_service()
        return {
            "total_videos":   len(videos_dicts),
            "metadata":       es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


async def _extract_channel_async(
    channel_id:            str,
    max_results:           int,
    include_transcription: bool,
    languages:             list[str] | None,
) -> dict[str, Any]:
    """Port of deprecated `_extract_channel_async` (crawler.py:L98-157)."""
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
                max_concurrent           = 5,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
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
    """Port of deprecated `_extract_playlist_async` (crawler.py:L160-219)."""
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
                max_concurrent           = 5,
                browser_refresh_interval = 10,
                max_retries              = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client          = es,
                    languages          = languages,
                    video_metadata     = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(
                        es, transcription_docs,
                    )
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


# =============================================================================
# Celery tasks (sync wrappers — Celery is sync by default)
# =============================================================================
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
        meta  = {"status": "extracting", "total": len(video_ids)},
    )
    result = asyncio.run(
        _extract_videos_async(video_ids, include_transcription, languages),
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
