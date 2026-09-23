"""ycs/extract — Celery tasks: yt-dlp metadata + Playwright transcripts → ES.
Three tasks
(one per ingestion mode: by video IDs, by channel, by playlist). Each:
  1. opens a fresh `AsyncElasticsearch` for the worker process
  2. dispatches `YtDlpExtractor.extract_{batch,channel,playlist}`
  3. bulk-indexes metadata via `domains.ycs.es_index`
  4. (if `include_transcription`) initializes Playwright service,
     runs `domains.ycs.transcript.service.fetch_transcriptions_batch`, and bulk-indexes transcripts
  5. always closes ES (and the transcript service when used)

Celery is sync; async work is wrapped in `asyncio.run(...)`. The
`@app.task(bind=True)` decorator gives access to `self.update_state(...)`
for progress reporting, which Flower and `GET /tasks/{id}` consume.
"""
from __future__ import annotations
import domains
import infra.celery.service
from . import service

import asyncio
import time
from typing import Any
from celery.utils.log import get_task_logger


logger = get_task_logger(__name__)


# Celery tasks (sync wrappers — Celery is sync by default)
@infra.celery.service.app.task(
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

    async def _run() -> dict[str, Any]:
        with infra.langfuse.service.session(
            "ycs-extract", session_id = self.request.id or "(no-request-id)",
        ), infra.otel.service.get_tracer().start_as_current_span(
            "ycs.extract.videos",
            attributes = {
                "coelho.langfuse.keep":        True,
                "coelho.langfuse.kind":        "workflow_root",
                "langfuse.trace.name":         "ycs.extract.videos",
                "ycs.extract.kind":            "videos",
                "ycs.video_count":             len(video_ids),
                "ycs.include_transcription":   include_transcription,
            },
        ):
            return await service.extract_videos_async(
                video_ids, include_transcription, languages,
                progress_cb = _progress,
                extract_id  = self.request.id,
            )

    t0 = time.monotonic()
    try:
        result = asyncio.run(_run())
    except Exception:
        domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
            kind = "extract_videos", outcome = "error", duration_s = time.monotonic() - t0,
        )
        raise
    domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
        kind = "extract_videos", outcome = "ok", duration_s = time.monotonic() - t0,
    )
    logger.info(f"[extract_videos] Done: {result}")
    return result


@infra.celery.service.app.task(
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

    async def _run() -> dict[str, Any]:
        with infra.langfuse.service.session(
            "ycs-extract",
            session_id = self.request.id or "(no-request-id)",
            channel_id = channel_id,
        ), infra.otel.service.get_tracer().start_as_current_span(
            "ycs.extract.channel",
            attributes = {
                "coelho.langfuse.keep":      True,
                "coelho.langfuse.kind":      "workflow_root",
                "langfuse.trace.name":       "ycs.extract.channel",
                "ycs.extract.kind":          "channel",
                "ycs.channel_id":            channel_id,
                "ycs.max_results":           max_results,
                "ycs.include_transcription": include_transcription,
            },
        ):
            return await service.extract_channel_async(
                channel_id, max_results, include_transcription, languages,
            )

    t0 = time.monotonic()
    try:
        result = asyncio.run(_run())
    except Exception:
        domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
            kind = "extract_channel", outcome = "error", duration_s = time.monotonic() - t0,
        )
        raise
    domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
        kind = "extract_channel", outcome = "ok", duration_s = time.monotonic() - t0,
    )
    logger.info(
        f"[extract_channel] Done: {result.get('total_videos')} videos",
    )
    return result


@infra.celery.service.app.task(
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

    async def _run() -> dict[str, Any]:
        with infra.langfuse.service.session(
            "ycs-extract", session_id = self.request.id or "(no-request-id)",
        ), infra.otel.service.get_tracer().start_as_current_span(
            "ycs.extract.playlist",
            attributes = {
                "coelho.langfuse.keep":      True,
                "coelho.langfuse.kind":      "workflow_root",
                "langfuse.trace.name":       "ycs.extract.playlist",
                "ycs.extract.kind":          "playlist",
                "ycs.playlist_id":           playlist_id,
                "ycs.max_results":           max_results,
                "ycs.include_transcription": include_transcription,
            },
        ):
            return await service.extract_playlist_async(
                playlist_id, max_results, include_transcription, languages,
            )

    t0 = time.monotonic()
    try:
        result = asyncio.run(_run())
    except Exception:
        domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
            kind = "extract_playlist", outcome = "error", duration_s = time.monotonic() - t0,
        )
        raise
    domains.ycs.runtime.observability.metrics.record_ycs_ingest_run(
        kind = "extract_playlist", outcome = "ok", duration_s = time.monotonic() - t0,
    )
    logger.info(
        f"[extract_playlist] Done: {result.get('total_videos')} videos",
    )
    return result
