"""
YouTube content extraction endpoints.

POST /search   - Search videos (returns results, no ES indexing)
POST /videos   - Extract + index selected videos by ID
POST /channel  - Extract + index channel videos
POST /playlist - Extract + index playlist videos

Extraction endpoints use:
- yt-dlp for metadata extraction
- Playwright CDP for transcriptions (bypasses IP blocking)
- ES indexes: metadata + transcriptions (normalized)
"""
from fastapi import APIRouter, HTTPException, Request

from schemas.inputs import SearchRequest, VideosRequest, ChannelRequest, PlaylistRequest
from .helpers import (
    get_extractor,
    fetch_transcriptions_batch,
    index_videos_to_elasticsearch,
    index_transcriptions_to_elasticsearch,
)

router = APIRouter()


@router.post("/search")
async def search_videos(payload: SearchRequest, request: Request):
    """
    Search YouTube videos by query.
    Returns search results with available metadata (fast, no ES indexing).
    Use /videos endpoint to extract and index selected videos.

    All filters use yt-dlp post-processing:
    - sort_by_date: Use ytsearchdate prefix (newest first)
    - duration: Preset or custom range in seconds
    - date_after/date_before: YYYYMMDD or relative (e.g., "today-2weeks")
    - min_views/max_views: View count range
    - min_likes: Minimum like count
    - is_live/live_status: Live stream filtering
    - availability: public, unlisted, premium_only, subscriber_only
    - title_contains/description_contains/channel_name: String filters

    String filter operators: *=contains, ^=starts_with, $=ends_with, ~=regex
    """
    if payload.max_results <= 0:
        raise HTTPException(
            status_code = 400,
            detail = "max_results must be > 0"
        )
    extractor = get_extractor()
    videos = await extractor.search(
        query = payload.query,
        max_results = payload.max_results,
        sort_by_date = payload.sort_by_date,
        # Duration
        duration = payload.duration,
        duration_min = payload.duration_min,
        duration_max = payload.duration_max,
        # Dates
        date_after = payload.date_after,
        date_before = payload.date_before,
        # View/like counts
        min_views = payload.min_views,
        max_views = payload.max_views,
        min_likes = payload.min_likes,
        # Live status
        is_live = payload.is_live,
        live_status = payload.live_status,
        # Availability
        availability = payload.availability,
        # Age limit
        age_limit = payload.age_limit,
        # String filters
        title_contains = payload.title_contains,
        description_contains = payload.description_contains,
        channel_name = payload.channel_name,
    )
    return {
        "query": payload.query,
        "filters": {
            "sort_by_date": payload.sort_by_date,
            "duration": payload.duration,
            "duration_min": payload.duration_min,
            "duration_max": payload.duration_max,
            "date_after": payload.date_after,
            "date_before": payload.date_before,
            "min_views": payload.min_views,
            "max_views": payload.max_views,
            "min_likes": payload.min_likes,
            "is_live": payload.is_live,
            "live_status": payload.live_status,
            "availability": payload.availability,
            "age_limit": payload.age_limit,
            "title_contains": payload.title_contains,
            "description_contains": payload.description_contains,
            "channel_name": payload.channel_name,
        },
        "total_results": len(videos),
        "videos": videos,
    }


@router.post("/videos")
async def get_videos(payload: VideosRequest, request: Request):
    """
    Fetch specific videos by ID.
    Extracts metadata and transcriptions, indexes to ElasticSearch.
    """
    es_client = request.app.state.es
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, 
            detail = "video_ids is required")
    extractor = get_extractor()
    videos = await extractor.extract_batch(payload.video_ids)
    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)
    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if payload.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service = request.app.state.transcript_service,
            es_client = es_client,
            languages = payload.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)
    return {
        "total_results": len(videos),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }


@router.post("/channel")
async def get_channel_videos(payload: ChannelRequest, request: Request):
    """
    Fetch videos from a YouTube channel.
    Extracts metadata and transcriptions, indexes to ElasticSearch.
    max_results=0 fetches ALL videos.
    """
    es_client = request.app.state.es
    extractor = get_extractor()
    result = await extractor.extract_channel(
        payload.channel_id,
        payload.max_results
    )
    if "error" in result and not result.get("videos"):
        raise HTTPException(
            status_code = 500, 
            detail = result["error"])
    videos = result.get("videos", [])
    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)
    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if payload.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service = request.app.state.transcript_service,
            es_client = es_client,
            languages = payload.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)
    return {
        "channel_id": result.get("channel_id"),
        "channel_name": result.get("channel_name"),
        "channel_url": result.get("channel_url"),
        "total_videos": len(videos),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }


@router.post("/playlist")
async def get_playlist_videos(payload: PlaylistRequest, request: Request):
    """
    Fetch videos from a YouTube playlist.
    Extracts metadata and transcriptions, indexes to ElasticSearch.
    max_results=0 fetches ALL videos.
    """
    es_client = request.app.state.es
    extractor = get_extractor()
    result = await extractor.extract_playlist(
        payload.playlist_id,
        payload.max_results
    )
    if "error" in result and not result.get("videos"):
        raise HTTPException(
            status_code = 500, 
            detail = result["error"])
    videos = result.get("videos", [])
    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)
    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if payload.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service = request.app.state.transcript_service,
            es_client = es_client,
            languages = payload.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)
    return {
        "playlist_id": result.get("playlist_id"),
        "playlist_title": result.get("playlist_title"),
        "playlist_url": result.get("playlist_url"),
        "total_videos": len(videos),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }
