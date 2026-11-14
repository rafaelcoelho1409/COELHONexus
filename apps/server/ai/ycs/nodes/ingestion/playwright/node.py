"""Fetch metadata and transcripts without writing either to Elasticsearch."""
from __future__ import annotations

import domains
from ....graphs.ingestion import entities, states

from typing import Any

from langgraph.runtime import Runtime


async def fetch_transcripts(
    state: states.IngestionChunkState,
    runtime: Runtime[entities.IngestionContext],
) -> dict[str, Any]:
    context = runtime.context
    video_ids = state.get("video_ids", [])
    videos = await context.extractor.extract_batch(video_ids)
    metadata_videos = [
        video.model_dump(exclude_none = False) if hasattr(video, "model_dump") else video
        for video in videos
    ]
    metadata_by_id = {
        video["id"]: domains.ycs.ingestion.extract.domain.project_video_meta(video)
        for video in metadata_videos
        if video.get("id")
    }
    successful_ids = set(metadata_by_id)
    successful_video_ids = [
        str(video["id"])
        for video in metadata_videos
        if video.get("id")
    ]
    failed_metadata_ids = [video_id for video_id in video_ids if video_id not in successful_ids]
    context.reporter.metadata_finished(metadata_by_id, failed_metadata_ids)

    transcription_docs: list[dict[str, Any]] = []
    cached_ids: list[str] = []
    no_transcript_ids: list[str] = []
    fetch_failed_ids = list(failed_metadata_ids)
    transcription_stats: dict[str, Any] = {
        "cached": 0,
        "fetched_ok": 0,
        "fetched_failed": len(failed_metadata_ids),
        "no_transcript": 0,
        "index_write_failed": 0,
    }

    if context.include_transcription and successful_ids:
        local_stats: dict[str, Any] = {}
        terminal_ids: set[str] = set()
        local_failed_ids: set[str] = set()
        local_no_transcript_ids: set[str] = set()

        def on_transcription_progress(
            _done: int,
            _total: int,
            video_id: str | None,
            success: bool,
            *,
            no_transcript: bool = False,
        ) -> None:
            if video_id:
                terminal_ids.add(video_id)
                context.reporter.transcription_finished(
                    video_id,
                    success = success,
                    no_transcript = no_transcript,
                )
                if no_transcript:
                    no_transcript_ids.append(video_id)
                    local_no_transcript_ids.add(video_id)
                elif not success:
                    fetch_failed_ids.append(video_id)
                    local_failed_ids.add(video_id)

        transcription_docs = await domains.ycs.ingestion.transcript.service.fetch_transcriptions_batch(
            successful_video_ids,
            transcript_service = context.transcript_service,
            es_client = context.es_client,
            languages = context.languages,
            video_metadata = {
                str(video["id"]): {
                    "channel_id": video.get("channel_id"),
                    "playlist_id": video.get("playlist_id"),
                }
                for video in metadata_videos
                if video.get("id")
            },
            progress_cb = on_transcription_progress,
            stats = local_stats,
            cancel_check = context.cancel_check,
            index_transcripts = False,
        )
        cached_ids = list(local_stats.get("cached_ids", []))
        cancelled = await context.cancel_check() if context.cancel_check else False
        unreported_ids = sorted(successful_ids - terminal_ids) if not cancelled else []
        for video_id in unreported_ids:
            context.reporter.transcription_finished(video_id, success = False)
            fetch_failed_ids.append(video_id)
        no_transcript_ids.extend(local_no_transcript_ids - set(no_transcript_ids))
        fetch_failed_ids.extend(local_failed_ids - set(fetch_failed_ids))
        transcription_stats.update({
            "cached": int(local_stats.get("cached", 0) or 0),
            "fetched_ok": int(local_stats.get("fetched_ok", 0) or 0),
            "fetched_failed": max(
                int(local_stats.get("fetched_failed", 0) or 0),
                len(local_failed_ids) + len(unreported_ids),
            ) + len(failed_metadata_ids),
            "no_transcript": int(local_stats.get("no_transcript", 0) or 0),
            "index_write_failed": 0,
        })

    return {
        "metadata_videos": metadata_videos,
        "transcript_docs": transcription_docs,
        "cached_ids": cached_ids,
        "no_transcript_ids": sorted(set(no_transcript_ids)),
        "fetch_failed_ids": sorted(set(fetch_failed_ids)),
        "transcription_stats": transcription_stats,
    }


__all__ = ["fetch_transcripts"]
