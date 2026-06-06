"""ycs/content — HTTP surface (sync yt-dlp search + 3 Celery dispatchers).

Wave 4 additions per `docs/YCS-PORT-PLAN-2026-06-06.md`:
  POST /videos    queues `extract_videos`   (specific IDs)
  POST /channel   queues `extract_channel`  (all videos in a channel)
  POST /playlist  queues `extract_playlist` (all videos in a playlist)

The sync `POST /search` (Wave 1) stays untouched. Dispatch endpoints
return immediately with a Celery task_id — clients poll
`/api/v1/ycs/admin/task/{id}` for progress (Wave 5).

Errors from the yt-dlp subprocess translate to 502 (upstream failure) /
504 (timeout) so the FastHTML form can show a sensible message."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from domains.ycs.content import (
    SearchRequest,
    SearchResponse,
    YtDlpJsonParseError,
    YtDlpSubprocessError,
    YtDlpTimeoutError,
    get_search_service,
)
from domains.ycs.extract import (
    ChannelRequest,
    PlaylistRequest,
    VideosRequest,
)


router = APIRouter()


@router.post("/search", response_model = SearchResponse)
async def search_videos(payload: SearchRequest) -> SearchResponse:
    """Synchronously search YouTube via yt-dlp `ytsearch*` and return
    snippets. No persistence — deprecated `helpers.py:L161-340`."""
    svc = get_search_service()
    try:
        return await svc.search(payload)
    except YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except YtDlpJsonParseError as e:
        raise HTTPException(
            status_code = 502, detail = f"yt-dlp output not JSON: {e}",
        )


@router.post("/videos")
async def get_videos(payload: VideosRequest) -> dict:
    """Extract specific videos by ID → ES (Celery background task).
    Returns immediately with task_id.

    Direct port of deprecated `routers/v1/youtube/content.py:L70-89`."""
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, detail = "video_ids is required",
        )
    from domains.ycs.extract.task import extract_videos
    task = extract_videos.delay(
        payload.video_ids,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.post("/channel")
async def get_channel_videos(payload: ChannelRequest) -> dict:
    """Extract all channel videos → ES (Celery background task).
    `max_results=0` fetches ALL videos.

    Direct port of deprecated `routers/v1/youtube/content.py:L92-109`."""
    from domains.ycs.extract.task import extract_channel
    task = extract_channel.delay(
        payload.channel_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.post("/playlist")
async def get_playlist_videos(payload: PlaylistRequest) -> dict:
    """Extract all playlist videos → ES (Celery background task).
    `max_results=0` fetches ALL videos.

    Direct port of deprecated `routers/v1/youtube/content.py:L112-129`."""
    from domains.ycs.extract.task import extract_playlist
    task = extract_playlist.delay(
        payload.playlist_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }
