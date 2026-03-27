import asyncio
import gc
import orjson
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
    extract_video_metadata_async,
    slugify
)


def log(msg: str):
    """Print log message with flush for real-time output."""
    print(f"[YOUTUBE] {msg}", flush = True)


router = APIRouter()

# Safety cap to prevent OOM when fetching all videos
MAX_VIDEOS_LIMIT = 100


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
    query = search_config.query
    max_results = search_config.max_results or 10
    redis_key = f"coelhonexus:youtube:search:{slugify(query)}:videos"
    log(f"[SEARCH] Query: {query}")
    log(f"[SEARCH] Max results: {max_results}")
    log(f"[SEARCH] Redis key: {redis_key}")
    # Initialize Redis JSON array
    await redis_aio.delete(redis_key)
    await redis_aio.json().set(redis_key, "$", [])
    # Search and stream to Redis
    filters = build_filters(search_config)
    search = Search(query, filters=filters)
    count = 0
    for v in search.videos:
        if count >= max_results:
            del v
            break
        video_data = extract_video_metadata(v)
        await redis_aio.json().arrappend(redis_key, "$", video_data)
        del v
        count += 1
        if count % 10 == 1:
            log(f"[SEARCH] Batch sample #{count}: {orjson.dumps(video_data).decode()}")
        elif count % 10 == 0:
            log(f"[SEARCH] Progress: {count} videos saved to Redis...")
            gc.collect()
        del video_data
    gc.collect()
    log(f"[SEARCH] Complete: {count} videos saved to Redis")
    return {
        "status": "completed",
        "query": query,
        "total": count,
        "redis_key": redis_key,
        "fetch_command": f"redis-cli JSON.GET {redis_key} '$'"
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
    video_ids = search_config.video_ids
    redis_key = f"coelhonexus:youtube:videos:{video_ids[0]}:batch"
    log(f"[VIDEOS] Fetching {len(video_ids)} videos")
    log(f"[VIDEOS] Video IDs: {video_ids}")
    log(f"[VIDEOS] Redis key: {redis_key}")
    # Initialize Redis JSON array
    await redis_aio.delete(redis_key)
    await redis_aio.json().set(redis_key, "$", [])
    # Fetch all videos concurrently
    async def fetch_video(video_id: str) -> dict:
        url = f"https://www.youtube.com/watch?v={video_id}"
        video = AsyncYouTube(url)
        return await extract_video_metadata_async(video, video_id)
    videos = await asyncio.gather(*[
        fetch_video(vid) for vid in video_ids
    ])
    # Stream to Redis
    count = 0
    for video_data in videos:
        await redis_aio.json().arrappend(redis_key, "$", video_data)
        count += 1
        if count % 10 == 1:
            log(f"[VIDEOS] Batch sample #{count}: {orjson.dumps(video_data).decode()}")
        elif count % 10 == 0:
            log(f"[VIDEOS] Progress: {count} videos saved to Redis...")
    log(f"[VIDEOS] Complete: {count} videos saved to Redis")
    return {
        "status": "completed",
        "total": count,
        "redis_key": redis_key,
        "fetch_command": f"redis-cli JSON.GET {redis_key} '$'"
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
    channel_id = search_config.channel_id
    channel_url = "https://www.youtube.com/@" + channel_id
    redis_key = f"coelhonexus:youtube:channel:{channel_id}:videos"
    log(f"[CHANNEL] Fetching: {channel_url}")
    log(f"[CHANNEL] Redis key: {redis_key}")
    channel = Channel(channel_url)
    # channel.length returns "851 videos" - extract number (approximate)
    length_str = str(channel.length or "0")
    total_videos = int(''.join(filter(str.isdigit, length_str)) or 0)
    offset = int(search_config.offset or 0)
    requested = int(search_config.max_results or 0)
    # max_results=0 means fetch ALL videos automatically
    fetch_all = (requested == 0)
    limit = None if fetch_all else min(requested, MAX_VIDEOS_LIMIT)
    log(f"[CHANNEL] Total: ~{total_videos}, fetch_all={fetch_all}, limit={limit}")
    # Clear existing data if starting fresh (offset=0)
    if offset == 0:
        await redis_aio.delete(redis_key)
        await redis_aio.json().set(redis_key, "$", [])
        log(f"[CHANNEL] Initialized empty JSON array in Redis")
    # Stream videos to Redis incrementally (memory-safe)
    count = 0
    for i, v in enumerate(channel.videos):
        if i < offset:
            del v
            continue
        # Extract and immediately append to RedisJSON array
        video_data = extract_video_metadata(v)
        await redis_aio.json().arrappend(redis_key, "$", video_data)
        del v
        count += 1
        # Log first video of each batch of 10
        if count % 10 == 1:
            log(f"[CHANNEL] Batch sample #{count}: {orjson.dumps(video_data).decode()}")
        # Log progress + garbage collection every 10 videos
        elif count % 10 == 0:
            log(f"[CHANNEL] Progress: {count} videos saved to Redis...")
            gc.collect()
        del video_data
        # If not fetching all, stop at limit
        if limit and count >= limit:
            break
    gc.collect()
    log(f"[CHANNEL] Complete: {count} videos saved to Redis")
    return {
        "status": "completed",
        "channel_url": channel_url,
        "channel_name": channel.channel_name,
        "channel_description": channel.description,
        "last_updated": str(channel.last_updated),
        "total_videos": total_videos,
        "saved": count,
        "redis_key": redis_key,
        "fetch_command": f"redis-cli JSON.GET {redis_key} '$'"
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
    playlist_id = search_config.playlist_id
    playlist_url = "https://www.youtube.com/playlist?list=" + playlist_id
    redis_key = f"coelhonexus:youtube:playlist:{playlist_id}:videos"
    log(f"[PLAYLIST] Fetching: {playlist_url}")
    log(f"[PLAYLIST] Redis key: {redis_key}")
    playlist = Playlist(playlist_url)
    # playlist.length may return "X videos" - extract number (approximate)
    length_str = str(playlist.length or "0")
    total_videos = int(''.join(filter(str.isdigit, length_str)) or 0)
    offset = int(search_config.offset or 0)
    requested = int(search_config.max_results or 0)
    # max_results=0 means fetch ALL videos automatically
    fetch_all = (requested == 0)
    limit = None if fetch_all else min(requested, MAX_VIDEOS_LIMIT)
    log(f"[PLAYLIST] Total: ~{total_videos}, fetch_all={fetch_all}, limit={limit}")
    # Clear existing data if starting fresh (offset=0)
    if offset == 0:
        await redis_aio.delete(redis_key)
        await redis_aio.json().set(redis_key, "$", [])
        log(f"[PLAYLIST] Initialized empty JSON array in Redis")
    # Stream videos to Redis incrementally (memory-safe)
    count = 0
    for i, v in enumerate(playlist.videos):
        if i < offset:
            del v
            continue
        # Extract and immediately append to RedisJSON array
        video_data = extract_video_metadata(v)
        await redis_aio.json().arrappend(redis_key, "$", video_data)
        del v
        count += 1
        # Log first video of each batch of 10
        if count % 10 == 1:
            log(f"[PLAYLIST] Batch sample #{count}: {orjson.dumps(video_data).decode()}")
        # Log progress + garbage collection every 10 videos
        elif count % 10 == 0:
            log(f"[PLAYLIST] Progress: {count} videos saved to Redis...")
            gc.collect()
        del video_data
        # If not fetching all, stop at limit
        if limit and count >= limit:
            break
    gc.collect()
    log(f"[PLAYLIST] Complete: {count} videos saved to Redis")
    return {
        "status": "completed",
        "playlist_url": playlist_url,
        "playlist_title": playlist.title,
        "owner": playlist.owner,
        "last_updated": str(playlist.last_updated),
        "total_videos": total_videos,
        "saved": count,
        "redis_key": redis_key,
        "fetch_command": f"redis-cli JSON.GET {redis_key} '$'"
    }



@router.post("/transcriptions")
async def get_transcriptions(payload: TranscriptionRequest):
    """Fetch transcriptions for multiple videos concurrently."""
    def fetch_transcript(video_id: str, languages: list[str] | None) -> dict:
        ytt_api = YouTubeTranscriptApi()
        try:
            if languages:
                fetched = ytt_api.fetch(
                    video_id, 
                    languages = languages)
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
