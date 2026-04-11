"""
Crawler Tasks — YouTube metadata extraction + transcription

CONCEPT: These tasks wrap the existing async extraction logic from helpers.py
and run it inside Celery's synchronous worker process.

Celery tasks are sync by default. We use asyncio.run() to bridge to
the existing async functions (yt-dlp subprocess, Playwright CDP, ES indexing).

Each task reports progress via self.update_state() which Flower displays
in real-time and the GET /tasks/{id} endpoint returns.
"""
import asyncio
import os
import sys

# Ensure /app is in Python path (Celery worker may not inherit it)
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


def _get_clients():
    """Create fresh async clients for the worker process."""
    from elasticsearch import AsyncElasticsearch

    es = AsyncElasticsearch(
        hosts = [os.environ["ELASTICSEARCH_HOST"]],
        basic_auth = (
            os.environ["ELASTICSEARCH_USERNAME"],
            os.environ.get("ELASTICSEARCH_PASSWORD", ""),
        ),
        verify_certs = False,
    )
    return es


async def _extract_videos_async(video_ids, include_transcription, languages):
    """Async implementation of video extraction + ES indexing."""
    from routers.v1.youtube.helpers import (
        get_extractor,
        fetch_transcriptions_batch,
        index_videos_to_elasticsearch,
        index_transcriptions_to_elasticsearch,
        init_transcript_service,
        close_transcript_service,
    )

    es = _get_clients()
    extractor = get_extractor()

    try:
        # Extract metadata
        videos = await extractor.extract_batch(video_ids)

        # Index metadata to ES
        es_metadata = await index_videos_to_elasticsearch(es, videos)

        # Fetch transcriptions if requested
        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
            video_metadata = {
                v["id"]: {"channel_id": v.get("channel_id"), "playlist_id": v.get("playlist_id")}
                for v in videos if v.get("id")
            }
            # Init Playwright for this worker
            transcript_service = await init_transcript_service(
                max_concurrent = 5,
                browser_refresh_interval = 10,
                max_retries = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client = es,
                    languages = languages,
                    video_metadata = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(es, transcription_docs)
            finally:
                await close_transcript_service()

        return {
            "total_videos": len(videos),
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


async def _extract_channel_async(channel_id, max_results, include_transcription, languages):
    """Async implementation of channel extraction + ES indexing."""
    from routers.v1.youtube.helpers import (
        get_extractor,
        fetch_transcriptions_batch,
        index_videos_to_elasticsearch,
        index_transcriptions_to_elasticsearch,
        init_transcript_service,
        close_transcript_service,
    )

    es = _get_clients()
    extractor = get_extractor()

    try:
        result = await extractor.extract_channel(channel_id, max_results)
        videos = result.get("videos", [])

        es_metadata = await index_videos_to_elasticsearch(es, videos)

        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
            channel_id_val = result.get("channel_id")
            video_metadata = {
                v["id"]: {"channel_id": channel_id_val, "playlist_id": v.get("playlist_id")}
                for v in videos if v.get("id")
            }
            transcript_service = await init_transcript_service(
                max_concurrent = 5,
                browser_refresh_interval = 10,
                max_retries = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client = es,
                    languages = languages,
                    video_metadata = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(es, transcription_docs)
            finally:
                await close_transcript_service()

        return {
            "channel_id": result.get("channel_id"),
            "channel_name": result.get("channel_name"),
            "total_videos": len(videos),
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


async def _extract_playlist_async(playlist_id, max_results, include_transcription, languages):
    """Async implementation of playlist extraction + ES indexing."""
    from routers.v1.youtube.helpers import (
        get_extractor,
        fetch_transcriptions_batch,
        index_videos_to_elasticsearch,
        index_transcriptions_to_elasticsearch,
        init_transcript_service,
        close_transcript_service,
    )

    es = _get_clients()
    extractor = get_extractor()

    try:
        result = await extractor.extract_playlist(playlist_id, max_results)
        videos = result.get("videos", [])

        es_metadata = await index_videos_to_elasticsearch(es, videos)

        es_transcriptions = {"indexed": 0, "failed": 0}
        if include_transcription:
            valid_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
            playlist_id_val = result.get("playlist_id")
            video_metadata = {
                v["id"]: {"channel_id": v.get("channel_id"), "playlist_id": playlist_id_val}
                for v in videos if v.get("id")
            }
            transcript_service = await init_transcript_service(
                max_concurrent = 5,
                browser_refresh_interval = 10,
                max_retries = 3,
            )
            try:
                transcription_docs = await fetch_transcriptions_batch(
                    valid_ids,
                    transcript_service = transcript_service,
                    es_client = es,
                    languages = languages,
                    video_metadata = video_metadata,
                )
                if transcription_docs:
                    es_transcriptions = await index_transcriptions_to_elasticsearch(es, transcription_docs)
            finally:
                await close_transcript_service()

        return {
            "playlist_id": result.get("playlist_id"),
            "playlist_title": result.get("playlist_title"),
            "total_videos": len(videos),
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        }
    finally:
        await es.close()


# =============================================================================
# Celery Tasks
# =============================================================================
@app.task(bind = True, name = "tasks.crawler.extract_videos")
def extract_videos(self, video_ids, include_transcription = True, languages = None):
    """Extract metadata + transcripts for specific video IDs → ES."""
    logger.info(f"[extract_videos] Starting: {len(video_ids)} videos")
    self.update_state(state = "PROGRESS", meta = {"status": "extracting", "total": len(video_ids)})
    result = asyncio.run(_extract_videos_async(video_ids, include_transcription, languages))
    logger.info(f"[extract_videos] Done: {result}")
    return result


@app.task(bind = True, name = "tasks.crawler.extract_channel")
def extract_channel(self, channel_id, max_results = 0, include_transcription = True, languages = None):
    """Extract all channel videos → ES."""
    logger.info(f"[extract_channel] Starting: {channel_id} (max={max_results})")
    self.update_state(state = "PROGRESS", meta = {"status": "extracting", "channel_id": channel_id})
    result = asyncio.run(_extract_channel_async(channel_id, max_results, include_transcription, languages))
    logger.info(f"[extract_channel] Done: {result.get('total_videos')} videos")
    return result


@app.task(bind = True, name = "tasks.crawler.extract_playlist")
def extract_playlist(self, playlist_id, max_results = 0, include_transcription = True, languages = None):
    """Extract all playlist videos → ES."""
    logger.info(f"[extract_playlist] Starting: {playlist_id} (max={max_results})")
    self.update_state(state = "PROGRESS", meta = {"status": "extracting", "playlist_id": playlist_id})
    result = asyncio.run(_extract_playlist_async(playlist_id, max_results, include_transcription, languages))
    logger.info(f"[extract_playlist] Done: {result.get('total_videos')} videos")
    return result
