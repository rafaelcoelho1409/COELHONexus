"""YCS feature router — wizard sub-slices.

POST /runs    body={video_url, question}        → legacy single-page Q&A
POST /search  body={query, ...filters}          → yt-dlp metadata search
                                                    (no ingest; feeds Source
                                                    stage results grid)

Synchronous for v0. When the surface grows we split per-concern
(routers/v1/youtube/{runs,search,ingestion}.py) and move heavy ingestion
to Celery, mirroring routers/v1/docs_distiller/.
"""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from services.youtube.rag import answer, index_video
from services.youtube.search import (
    enumerate_channel,
    enumerate_playlist,
    enumerate_videos,
    search_videos,
)


router = APIRouter()


class RunRequest(BaseModel):
    video_url: str = Field(..., description="YouTube watch URL")
    question: str = Field(..., min_length=1, description="Question to answer over the video transcript")


@router.post("/runs")
async def create_run(req: RunRequest) -> dict:
    """Ingest one video (idempotent) then answer one question over it."""
    indexed = await index_video(req.video_url)
    response = await answer(req.question)
    return {"indexed": indexed, **response}


class SearchRequest(BaseModel):
    """YouTube metadata search — no ingestion. Mirrors legacy POST /search."""
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1)
    max_results: int = Field(10, ge=1, le=100)
    sort_by_date: bool = False
    duration: Literal[
        "Under 4 minutes", "4 - 20 minutes", "Over 20 minutes",
    ] | None = None
    duration_min: int | None = Field(None, ge=0)
    duration_max: int | None = Field(None, ge=0)
    date_after: str | None = None
    date_before: str | None = None
    min_views: int | None = Field(None, ge=0)
    max_views: int | None = Field(None, ge=0)
    min_likes: int | None = Field(None, ge=0)
    is_live: bool | None = None
    live_status: Literal[
        "not_live", "is_live", "is_upcoming", "was_live", "post_live",
    ] | None = None
    availability: Literal[
        "public", "unlisted", "private",
        "premium_only", "subscriber_only", "needs_auth",
    ] | None = None
    age_limit: int | None = Field(None, ge=0)
    title_contains: str | None = None
    description_contains: str | None = None
    channel_name: str | None = None


@router.post("/search")
async def search(req: SearchRequest) -> dict:
    """yt-dlp metadata search with rich filters. No transcript fetch,
    no embedding, no upsert — strictly a curation step that feeds
    the Source stage's results grid."""
    videos = await search_videos(**req.model_dump(exclude_none=True))
    return {
        "type": "search",
        "query": req.query,
        "total_results": len(videos),
        "videos": videos,
    }


class VideosRequest(BaseModel):
    """Direct mode — enumerate explicit video IDs/URLs."""
    model_config = ConfigDict(extra="forbid")
    video_inputs: list[str] = Field(..., min_length=1, max_length=200)


@router.post("/videos")
async def enumerate_videos_endpoint(req: VideosRequest) -> dict:
    """Fetch metadata for an explicit list of video IDs/URLs. No ingestion."""
    videos = await enumerate_videos(req.video_inputs)
    return {
        "type": "videos",
        "total_results": len(videos),
        "videos": videos,
    }


class PlaylistRequest(BaseModel):
    """Direct mode — enumerate a single playlist."""
    model_config = ConfigDict(extra="forbid")
    playlist: str = Field(..., min_length=1)
    max_results: int = Field(0, ge=0, le=500)


@router.post("/playlist")
async def enumerate_playlist_endpoint(req: PlaylistRequest) -> dict:
    """Enumerate a YouTube playlist (URL or bare ID). 0 = all (capped at 500)."""
    videos = await enumerate_playlist(req.playlist, req.max_results)
    return {
        "type": "playlist",
        "total_results": len(videos),
        "videos": videos,
    }


class ChannelRequest(BaseModel):
    """Direct mode — enumerate a single channel's /videos tab."""
    model_config = ConfigDict(extra="forbid")
    channel: str = Field(..., min_length=1)
    max_results: int = Field(30, ge=0, le=500)


@router.post("/channel")
async def enumerate_channel_endpoint(req: ChannelRequest) -> dict:
    """Enumerate a channel's /videos tab. Accepts full URL, @handle, or UCxxx.
    Default 30 most recent; 0 = all (capped at 500)."""
    videos = await enumerate_channel(req.channel, req.max_results)
    return {
        "type": "channel",
        "total_results": len(videos),
        "videos": videos,
    }
