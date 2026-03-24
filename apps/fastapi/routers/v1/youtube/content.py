from fastapi import (
    APIRouter, 
    HTTPException, 
    Request
)
from pytubefix import YouTube, Channel, Playlist
from pytubefix.contrib.search import Search, Filter

from schemas.inputs import YouTubeSearchConfig
from .helpers import build_filters


router = APIRouter()


# =============================================================================
# Endpoints
# =============================================================================
@router.put("/config")
async def create_search_config(config: YouTubeSearchConfig, request: Request):
    redis_aio = request.app.state.redis_aio
    # Apply defaults for PUT (full replace)
    data = config.model_dump(exclude_none = True)
    data.setdefault("search_type", "search")
    data.setdefault("max_results", 10)
    data.setdefault("sort_by", "Relevance")
    await redis_aio.json().set(
        "coelhonexus:youtube:search:config",
        "$",
        data
    )
    return {"status": "saved", "config": data}

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
            detail = "Config not found. Use PUT to create.")
    # Merge only provided fields
    merged = {
        **existing[0], 
        **config.model_dump(exclude_none = True)}
    await redis_aio.json().set(
        "coelhonexus:youtube:search:config",
        "$",
        merged
    )
    return {"status": "updated", "config": merged}

@router.get("/search")
async def search_results(query: str, request: Request):
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
    max_results = search_config.max_results or 10
    search_results = Search(
        query,
        filters = filters).videos[:max_results]
    search_results_dict = {}
    search_results_dict[query] = {
        "title": [video.title for video in search_results],
        "author": [video.author for video in search_results],
        "publish_date": [video.publish_date for video in search_results],
        "views": [video.views for video in search_results],
        "length": [video.length for video in search_results],
        "captions": [str(list(video.captions.lang_code_index.keys())) for video in search_results],
        #"keywords": [video.keywords for video in search_results],
        #"description": [video.description for video in search_results],
        "video_id": [video.video_id for video in search_results],
    }
    return search_results_dict
