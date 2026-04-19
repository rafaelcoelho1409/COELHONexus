"""
YouTube content extraction endpoints.

POST /search   - Search videos (sync — fast, returns immediately)
POST /videos   - Extract + index selected videos by ID (Celery background task)
POST /channel  - Extract + index channel videos (Celery background task)
POST /playlist - Extract + index playlist videos (Celery background task)

Heavy extraction endpoints always run as Celery background tasks.
Returns task_id immediately — poll GET /api/v1/tasks/{task_id} for progress.
"""
from fastapi import (
    APIRouter, 
    HTTPException, 
    Request
)

from schemas.youtube.inputs import (
    SearchRequest,
    VideosRequest,
    ChannelRequest,
    PlaylistRequest
)
from .helpers import get_extractor


router = APIRouter()

@router.post("/search")
async def search_videos(
    payload: SearchRequest, 
    request: Request):
    """
    Search YouTube videos by query (sync — fast, no ES indexing).
    Returns search results with available metadata.
    Use /videos endpoint to extract and index selected videos.
    """
    if payload.max_results <= 0:
        raise HTTPException(
            status_code = 400, 
            detail = "max_results must be > 0")
    extractor = get_extractor()
    videos = await extractor.search(
        query = payload.query,
        max_results = payload.max_results,
        sort_by_date = payload.sort_by_date,
        duration = payload.duration,
        duration_min = payload.duration_min,
        duration_max = payload.duration_max,
        date_after = payload.date_after,
        date_before = payload.date_before,
        min_views = payload.min_views,
        max_views = payload.max_views,
        min_likes = payload.min_likes,
        is_live = payload.is_live,
        live_status = payload.live_status,
        availability = payload.availability,
        age_limit = payload.age_limit,
        title_contains = payload.title_contains,
        description_contains = payload.description_contains,
        channel_name = payload.channel_name,
    )
    return {
        "query": payload.query,
        "total_results": len(videos),
        "videos": videos,
    }


@router.post("/videos")
async def get_videos(payload: VideosRequest):
    """
    Extract specific videos by ID → ES (Celery background task).
    Returns immediately with task_id.
    """
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, 
            detail = "video_ids is required")
    from tasks.youtube.crawler import extract_videos
    task = extract_videos.delay(
        payload.video_ids,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}


@router.post("/channel")
async def get_channel_videos(payload: ChannelRequest):
    """
    Extract all channel videos → ES (Celery background task).
    Returns immediately with task_id.
    max_results=0 fetches ALL videos.
    """
    from tasks.youtube.crawler import extract_channel
    task = extract_channel.delay(
        payload.channel_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}


@router.post("/playlist")
async def get_playlist_videos(payload: PlaylistRequest):
    """
    Extract all playlist videos → ES (Celery background task).
    Returns immediately with task_id.
    max_results=0 fetches ALL videos.
    """
    from tasks.youtube.crawler import extract_playlist
    task = extract_playlist.delay(
        payload.playlist_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    return {
        "task_id": task.id, 
        "status": "queued", 
        "endpoint": f"/api/v1/tasks/{task.id}"}
