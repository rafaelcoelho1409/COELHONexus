import asyncio
from fastapi import (
    APIRouter,
    HTTPException,
    Request
)
from pytubefix import AsyncYouTube, Channel, Playlist
from pytubefix.contrib.search import Search, Filter
from youtube_transcript_api import YouTubeTranscriptApi

from schemas.inputs import (
    YouTubeSearchConfig,
    TranscriptionRequest
)
from .helpers import (
    build_filters, 
    extract_video_metadata, 
    extract_video_metadata_async
)

router = APIRouter()


# =============================================================================
# Endpoints
# =============================================================================
@router.put("/config")
async def replace_search_config(config: YouTubeSearchConfig, request: Request):
    """Full replacement of config. Resets all fields not provided."""
    redis_aio = request.app.state.redis_aio
    data = config.model_dump(exclude_none = True)
    data.setdefault("query", "alborghetti")
    data.setdefault("max_results", 10)
    data.setdefault("sort_by", "Relevance")
    await redis_aio.json().set(
        "coelhonexus:youtube:search:config",
        "$",
        data
    )
    return {"status": "replaced", "config": data}

@router.patch("/config")
async def patch_search_config(config: YouTubeSearchConfig, request: Request):
    redis_aio = request.app.state.redis_aio
    # Get existing config
    existing = await redis_aio.json().get(
        "coelhonexus:youtube:search:config", 
        "$")
    if not existing:
        raise HTTPException(
            status_code = 404,
            detail = "Config not found.")
    # Merge only provided fields
    merged = {
        **existing[0], 
        **config.model_dump(exclude_none = True)}
    await redis_aio.json().set(
        "coelhonexus:youtube:search:config",
        "$",
        merged
    )
    return {
        "status": "updated", 
        "config": merged}

@router.get("/search")
async def search_results(request: Request):
    redis_aio = request.app.state.redis_aio
    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$")
    if not search_config:
        raise HTTPException(
            status_code = 404,
            detail = "Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])
    # Search
    filters = build_filters(search_config)
    results = Search(
        search_config.query,
        filters = filters).videos[:search_config.max_results]
    videos = [extract_video_metadata(v) for v in results]
    return {
        search_config.query: {
            "video_id": [v["video_id"] for v in videos],
            "title": [v["title"] for v in videos],
            "author": [v["author"] for v in videos],
            "publish_date": [v["publish_date"] for v in videos],
            "views": [v["views"] for v in videos],
            "length": [v["length"] for v in videos],
            "captions": [v["captions"] for v in videos],
            #"keywords": [v["keywords"] for v in videos],
            #"description": [v["description"] for v in videos],
        }
    }

@router.get("/videos")
async def get_youtube_videos(request: Request):
    """Fetch multiple videos concurrently using AsyncYouTube."""
    redis_aio = request.app.state.redis_aio
    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$")
    if not search_config:
        raise HTTPException(
            status_code = 404,
            detail = "Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])
    # Videos
    if not search_config.video_ids:
        raise HTTPException(
            status_code = 404,
            detail = "Video IDs not found")
    # Fetch all videos concurrently
    async def fetch_video(video_id: str) -> dict:
        url = f"https://www.youtube.com/watch?v={video_id}"
        video = AsyncYouTube(url)
        return await extract_video_metadata_async(video, video_id)
    videos = await asyncio.gather(*[
        fetch_video(vid) for vid in search_config.video_ids
    ])
    return {
        "videos": {
            "video_id": [v["video_id"] for v in videos],
            "title": [v["title"] for v in videos],
            "author": [v["author"] for v in videos],
            "publish_date": [v["publish_date"] for v in videos],
            "views": [v["views"] for v in videos],
            "length": [v["length"] for v in videos],
            "captions": [v["captions"] for v in videos],
            #"keywords": [v["keywords"] for v in videos],
            #"description": [v["description"] for v in videos],
        }
    }


@router.get("/channel")
async def search_youtube_channel(request: Request):
    redis_aio = request.app.state.redis_aio
    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$")
    if not search_config:
        raise HTTPException(
            status_code = 404,
            detail = "Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])
    # Channel
    if search_config.channel_id is None:
        raise HTTPException(
            status_code = 404,
            detail = "Channel not found")
    channel_url = "https://www.youtube.com/@" + search_config.channel_id
    channel = Channel(channel_url)
    videos = [extract_video_metadata(v) for v in channel.videos[:search_config.max_results]]
    return {
        channel_url: {
            "video_id": [v["video_id"] for v in videos],
            "title": [v["title"] for v in videos],
            "author": [v["author"] for v in videos],
            "publish_date": [v["publish_date"] for v in videos],
            "views": [v["views"] for v in videos],
            "length": [v["length"] for v in videos],
            "captions": [v["captions"] for v in videos],
            #"keywords": [v["keywords"] for v in videos],
            #"description": [v["description"] for v in videos],
            "channel_name": channel.channel_name,
            "channel_description": channel.description,
            "last_updated": str(channel.last_updated),
        }
    }

@router.get("/playlist")
async def search_youtube_playlist(request: Request):
    redis_aio = request.app.state.redis_aio
    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$")
    if not search_config:
        raise HTTPException(
            status_code = 404,
            detail = "Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])
    # Playlist
    if search_config.playlist_id is None:
        raise HTTPException(
            status_code = 404,
            detail = "Playlist not found")
    playlist_url = "https://www.youtube.com/playlist?list=" + search_config.playlist_id
    playlist = Playlist(playlist_url)
    videos = [extract_video_metadata(v) for v in playlist.videos[:search_config.max_results]]
    return {
        playlist_url: {
            "video_id": [v["video_id"] for v in videos],
            "title": [v["title"] for v in videos],
            "author": [v["author"] for v in videos],
            "publish_date": [v["publish_date"] for v in videos],
            "views": [v["views"] for v in videos],
            "length": [v["length"] for v in videos],
            "captions": [v["captions"] for v in videos],
            #"keywords": [v["keywords"] for v in videos],
            #"description": [v["description"] for v in videos],
        }
    }

@router.post("/transcriptions")
async def get_transcriptions(payload: TranscriptionRequest):
    """Fetch transcriptions for multiple videos concurrently."""
    def fetch_transcript(video_id: str, languages: list[str] | None) -> dict:
        ytt_api = YouTubeTranscriptApi()
        try:
            if languages:
                fetched = ytt_api.fetch(video_id, languages=languages)
            else:
                # Get first available transcript (any language)
                transcript_list = ytt_api.list(video_id)
                first_transcript = next(iter(transcript_list))
                fetched = first_transcript.fetch()
            return {
                "video_id": video_id,
                "language": fetched.language_code,
                "page_content": " ".join([snippet.text for snippet in fetched])}
        except Exception as e:
            return {
                "video_id": video_id,
                "error": str(e)}
    transcriptions = await asyncio.gather(*[
        asyncio.to_thread(fetch_transcript, vid, payload.languages)
        for vid in payload.video_ids
    ])
    return {"transcriptions": transcriptions}
