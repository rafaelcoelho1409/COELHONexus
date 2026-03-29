"""
YouTube content endpoints using yt-dlp subprocess for metadata extraction.
Transcriptions use Playwright CDP (bypasses IP blocking).
Data is indexed to two ES indexes: metadata + transcriptions (normalized).
"""
from fastapi import APIRouter, HTTPException, Request

from schemas.inputs import YouTubeSearchConfig, TranscriptionRequest
from .helpers import (
    get_extractor,
    fetch_transcriptions_batch,
    index_videos_to_elasticsearch,
    index_transcriptions_to_elasticsearch,
)

router = APIRouter()


# =============================================================================
# Endpoints
# =============================================================================
@router.put("/config")
async def replace_search_config(config: YouTubeSearchConfig, request: Request):
    """Full replacement of config. Resets all fields not provided."""
    redis_aio = request.app.state.redis_aio
    data = config.model_dump(exclude_none=True)
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
    existing = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$"
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Config not found.")
    merged = {**existing[0], **config.model_dump(exclude_none=True)}
    await redis_aio.json().set(
        "coelhonexus:youtube:search:config",
        "$",
        merged
    )
    return {"status": "updated", "config": merged}


@router.get("/search")
async def search_results(request: Request):
    """
    Search YouTube videos using yt-dlp subprocess.
    Extracts FULL metadata and indexes to ElasticSearch.
    max_results=0 is NOT allowed (raises 400 error).
    """
    redis_aio = request.app.state.redis_aio
    es_client = request.app.state.es
    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$"
    )
    if not search_config:
        raise HTTPException(
            status_code = 404, 
            detail = "Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])
    if not search_config.max_results or search_config.max_results <= 0:
        raise HTTPException(
            status_code = 400,
            detail = "max_results must be > 0 for /search. Use /channel or /playlist for all videos."
        )
    extractor = get_extractor()
    sort_by_date = search_config.sort_by == "Upload Date"
    videos = await extractor.search(
        search_config.query,
        search_config.max_results,
        sort_by_date=sort_by_date,
    )

    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)

    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if search_config.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service=request.app.state.transcript_service,
            es_client=es_client,
            languages=search_config.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)

    return {
        "query": search_config.query,
        "total_results": len(videos),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }


@router.get("/videos")
async def get_youtube_videos(request: Request):
    """
    Fetch multiple videos by ID using yt-dlp subprocess.
    Extracts FULL metadata and indexes to ElasticSearch.
    """
    redis_aio = request.app.state.redis_aio
    es_client = request.app.state.es

    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$"
    )
    if not search_config:
        raise HTTPException(status_code=404, detail="Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])

    if not search_config.video_ids:
        raise HTTPException(status_code=404, detail="Video IDs not found")

    extractor = get_extractor()
    videos = await extractor.extract_batch(search_config.video_ids)

    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)

    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if search_config.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service=request.app.state.transcript_service,
            es_client=es_client,
            languages=search_config.transcription_languages,
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


@router.get("/channel")
async def search_youtube_channel(request: Request):
    """
    Fetch videos from a YouTube channel using yt-dlp subprocess.
    Extracts FULL metadata and indexes to ElasticSearch.
    max_results=0 fetches ALL videos.
    """
    redis_aio = request.app.state.redis_aio
    es_client = request.app.state.es

    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$"
    )
    if not search_config:
        raise HTTPException(status_code=404, detail="Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])

    if not search_config.channel_id:
        raise HTTPException(status_code=404, detail="Channel ID not found")

    extractor = get_extractor()
    result = await extractor.extract_channel(
        search_config.channel_id,
        search_config.max_results or 0
    )

    if "error" in result and not result.get("videos"):
        raise HTTPException(status_code=500, detail=result["error"])

    videos = result.get("videos", [])

    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)

    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if search_config.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service=request.app.state.transcript_service,
            es_client=es_client,
            languages=search_config.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)

    return {
        "channel_id": result.get("channel_id"),
        "channel_name": result.get("channel_name"),
        "channel_url": result.get("channel_url"),
        "total_videos": result.get("total_videos"),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }


@router.get("/playlist")
async def search_youtube_playlist(request: Request):
    """
    Fetch videos from a YouTube playlist using yt-dlp subprocess.
    Extracts FULL metadata and indexes to ElasticSearch.
    max_results=0 fetches ALL videos.
    """
    redis_aio = request.app.state.redis_aio
    es_client = request.app.state.es

    search_config = await redis_aio.json().get(
        "coelhonexus:youtube:search:config",
        "$"
    )
    if not search_config:
        raise HTTPException(status_code=404, detail="Search config not found")
    search_config = YouTubeSearchConfig(**search_config[0])

    if not search_config.playlist_id:
        raise HTTPException(status_code=404, detail="Playlist ID not found")

    extractor = get_extractor()
    result = await extractor.extract_playlist(
        search_config.playlist_id,
        search_config.max_results or 0
    )

    if "error" in result and not result.get("videos"):
        raise HTTPException(status_code=500, detail=result["error"])

    videos = result.get("videos", [])

    # Index video metadata to ES
    es_metadata = await index_videos_to_elasticsearch(es_client, videos)

    # Fetch and index transcriptions if requested
    es_transcriptions = {"indexed": 0, "failed": 0}
    if search_config.include_transcription:
        video_ids = [v["id"] for v in videos if v.get("id") and "error" not in v]
        transcription_docs = await fetch_transcriptions_batch(
            video_ids,
            transcript_service=request.app.state.transcript_service,
            es_client=es_client,
            languages=search_config.transcription_languages,
        )
        if transcription_docs:
            es_transcriptions = await index_transcriptions_to_elasticsearch(es_client, transcription_docs)

    return {
        "playlist_id": result.get("playlist_id"),
        "playlist_title": result.get("playlist_title"),
        "playlist_url": result.get("playlist_url"),
        "total_videos": result.get("total_videos"),
        "elasticsearch": {
            "metadata": es_metadata,
            "transcriptions": es_transcriptions,
        },
    }


@router.post("/transcriptions")
async def get_transcriptions(payload: TranscriptionRequest, request: Request):
    """
    Fetch transcriptions for multiple videos with controlled concurrency.

    Uses Playwright CDP browser pool (bypasses IP blocking).
    - Semaphore limits concurrent browser contexts (default: 5)
    - Context pool for reuse (reduces memory overhead)
    - Exponential backoff retry on errors

    No batch size limit - semaphore ensures memory safety regardless of batch size.
    Large batches (500+ videos) will queue and process with controlled concurrency.
    """
    transcript_service = request.app.state.transcript_service
    transcriptions = await transcript_service.fetch_batch(payload.video_ids)
    return {"transcriptions": transcriptions}
