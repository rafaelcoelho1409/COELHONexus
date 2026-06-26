"""ycs/content — sync yt-dlp search + Celery dispatchers for video/channel/playlist ingestion.
yt-dlp errors translate to 502 (subprocess failure) / 504 (timeout)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from domains.ycs.content import (
    EnumerationResponse,
    SearchRequest,
    SearchResponse,
    YtDlpJsonParseError,
    YtDlpSubprocessError,
    YtDlpTimeoutError,
    get_search_service,
)
from domains.ycs.extract import (
    ChannelPipelineRequest,
    ChannelRequest,
    PlaylistPipelineRequest,
    PlaylistRequest,
    VideosRequest,
)


router = APIRouter()


@router.post("/search", response_model = SearchResponse)
async def search_videos(payload: SearchRequest) -> SearchResponse:
    """Synchronous yt-dlp `ytsearch*` — returns snippets, no persistence."""
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
    """Extract specific videos → ES (Celery). Bare extract only; the Videos tab uses `/videos/pipeline`."""
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


@router.post("/videos/pipeline")
async def get_videos_pipeline(
    payload: VideosRequest, request: Request,
) -> dict:
    """Full 3-phase pipeline (extract → Qdrant → Neo4j → invalidate). Chained at the API layer so
    each phase reports its own progress. Snapshots params to Redis to enable Rerun."""
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, detail = "video_ids is required",
        )
    from domains.ycs.pipeline_task import (
        dispatch_videos_pipeline,
        persist_pipeline_state,
    )
    phases = dispatch_videos_pipeline(
        video_ids             = payload.video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await persist_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id            = phases.get("extract", ""),
        video_ids             = payload.video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
        phases                = phases,
    )
    return {
        "status":     "queued",
        "phases":     phases,
        "video_ids":  payload.video_ids,
        "endpoint":   "/api/v1/ycs/admin/pipeline",
    }


@router.post("/videos/pipeline/{extract_id}/rerun")
async def rerun_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Re-fire the 3-phase chain from the Redis snapshot of a prior dispatch (24h TTL).
    Phase A skips existing ES transcripts; Phase B re-upserts (idempotent); Phase C skips tagged video_ids."""
    from domains.ycs.pipeline_task import (
        dispatch_videos_pipeline,
        load_pipeline_state,
        persist_pipeline_state,
    )
    state = await load_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id,
    )
    if not state:
        raise HTTPException(
            status_code = 404,
            detail      = (
                f"No saved pipeline state for {extract_id} — the rerun "
                f"window (24h) expired or the id is unknown."
            ),
        )
    phases = dispatch_videos_pipeline(
        video_ids             = state["video_ids"],
        include_transcription = state.get("include_transcription", True),
        languages             = state.get("languages"),
    )
    await persist_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id            = phases.get("extract", ""),
        video_ids             = state["video_ids"],
        include_transcription = state.get("include_transcription", True),
        languages             = state.get("languages"),
        phases                = phases,
    )
    return {
        "status":    "queued",
        "phases":    phases,
        "video_ids": state["video_ids"],
        "rerun_of":  extract_id,
    }


@router.get("/videos/pipeline/{extract_id}/state")
async def get_videos_pipeline_state(
    extract_id: str, request: Request,
) -> dict:
    """Return the saved dispatch state for a pipeline. Used to rehydrate `video_ids`+`phases` after
    page refresh or cross-tab navigation. 404 after the 24h Redis TTL."""
    from domains.ycs.pipeline_task import load_pipeline_state
    state = await load_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id,
    )
    if not state:
        raise HTTPException(
            status_code = 404,
            detail = (
                f"No saved state for {extract_id} — already expired "
                "(24h TTL) or unknown."
            ),
        )
    return state


@router.post("/videos/pipeline/{extract_id}/wipe")
async def wipe_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Wipe artifacts for `extract_id`, then revoke in-flight phases (wipe first, revoke second).
    Without revoke a mid-LLM-call Phase 3 writes orphan Document nodes the next Retry's skip-check finds.
    `__Entity__` nodes left intact — may be shared across other videos."""
    from domains.ycs.pipeline_task import (
        load_pipeline_state,
        revoke_pipeline_phases,
        wipe_videos_data,
    )
    state = await load_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id,
    )
    if not state or not state.get("video_ids"):
        raise HTTPException(
            status_code = 404,
            detail      = (
                f"No saved pipeline state for {extract_id} — already "
                f"expired (24h TTL) or unknown."
            ),
        )
    summary = await wipe_videos_data(
        video_ids   = state["video_ids"],
        neo4j_graph = getattr(request.app.state, "neo4j_graph", None),
    )
    phases: dict[str, str] = state.get("phases", {})
    phase_ids = [
        phases.get("extract",    ""),
        phases.get("qdrant",     ""),
        phases.get("neo4j",      ""),
        phases.get("invalidate", ""),
    ]
    revoke_outcomes = revoke_pipeline_phases(phase_ids)
    return {
        "status":          "wiped",
        "summary":         summary,
        "revoke_outcomes": revoke_outcomes,
    }


@router.post("/videos/pipeline/{extract_id}/stop")
async def stop_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Revoke all in-flight phases for `extract_id`. Preserves SUCCESS-state phases; idempotent
    Qdrant upserts and Neo4j skip-on-video_id let a rerun pick up cleanly."""
    from domains.ycs.pipeline_task import (
        load_pipeline_state,
        revoke_pipeline_phases,
    )
    state = await load_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id,
    )
    if not state or not state.get("phases"):
        raise HTTPException(
            status_code = 404,
            detail      = (
                f"No saved pipeline state for {extract_id} — already "
                f"expired (24h TTL) or unknown."
            ),
        )
    phases: dict[str, str] = state["phases"]
    phase_ids = [
        phases.get("extract", ""),
        phases.get("qdrant", ""),
        phases.get("neo4j", ""),
        phases.get("invalidate", ""),
    ]
    outcomes = revoke_pipeline_phases(phase_ids)
    return {
        "status":   "revoked",
        "phases":   phases,
        "outcomes": outcomes,
    }


@router.get("/videos/preview", response_model = EnumerationResponse)
async def preview_videos(
    ids:    str,
    limit:  int = 100,
    offset: int = 0,
) -> EnumerationResponse:
    """yt-dlp metadata fetch for a comma-separated `ids=` list. Same `EnumerationResponse` shape
    as channel/playlist so picker.js renders all three tabs with one shared module."""
    video_ids = [v.strip() for v in (ids or "").split(",") if v.strip()]
    if not video_ids:
        raise HTTPException(
            status_code = 400, detail = "ids is required (comma-separated)",
        )
    svc = get_search_service()
    try:
        return await svc.preview_videos(
            video_ids = video_ids, limit = limit, offset = offset,
        )
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


@router.get("/channel/videos", response_model = EnumerationResponse)
async def enumerate_channel_videos(
    id:     str,
    limit:  int = 100,
    offset: int = 0,
) -> EnumerationResponse:
    """Paginated channel video listing. Resolves any input shape (bare `UC…`, `@handle`, URL) to
    the uploads playlist for cheapest pagination. `total=None` when yt-dlp can't surface `playlist_count`."""
    svc = get_search_service()
    try:
        return await svc.enumerate_videos(
            source = "channel", raw_input = id,
            limit  = limit, offset = offset,
        )
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
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))


@router.get("/playlist/videos", response_model = EnumerationResponse)
async def enumerate_playlist_videos(
    id:     str,
    limit:  int = 100,
    offset: int = 0,
) -> EnumerationResponse:
    """Paginated playlist video listing. Accepts bare `PL…`/`UU…`, full `playlist?list=…`, or `watch?v=…&list=…` URLs."""
    svc = get_search_service()
    try:
        return await svc.enumerate_videos(
            source = "playlist", raw_input = id,
            limit  = limit, offset = offset,
        )
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
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))


@router.post("/channel/pipeline")
async def channel_pipeline(
    payload: ChannelPipelineRequest, request: Request,
) -> dict:
    """Enumerate ALL channel videos server-side, then dispatch the 3-phase pipeline. Bypasses the 100-per-page picker cap."""
    svc = get_search_service()
    try:
        video_ids = await svc.enumerate_all_video_ids(
            source = "channel", raw_input = payload.channel_id,
        )
    except YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))
    if not video_ids:
        raise HTTPException(
            status_code = 404,
            detail = f"No videos found in channel {payload.channel_id!r}",
        )
    from domains.ycs.pipeline_task import (
        dispatch_videos_pipeline,
        persist_pipeline_state,
    )
    phases = dispatch_videos_pipeline(
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await persist_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id            = phases.get("extract", ""),
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
        phases                = phases,
    )
    return {
        "status":    "queued",
        "phases":    phases,
        "video_ids": video_ids,
        "endpoint":  "/api/v1/ycs/admin/pipeline",
    }


@router.post("/playlist/pipeline")
async def playlist_pipeline(
    payload: PlaylistPipelineRequest, request: Request,
) -> dict:
    """Enumerate ALL playlist videos server-side, then dispatch the 3-phase pipeline."""
    svc = get_search_service()
    try:
        video_ids = await svc.enumerate_all_video_ids(
            source = "playlist", raw_input = payload.playlist_id,
        )
    except YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))
    if not video_ids:
        raise HTTPException(
            status_code = 404,
            detail = f"No videos found in playlist {payload.playlist_id!r}",
        )
    from domains.ycs.pipeline_task import (
        dispatch_videos_pipeline,
        persist_pipeline_state,
    )
    phases = dispatch_videos_pipeline(
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await persist_pipeline_state(
        getattr(request.app.state, "redis_aio", None),
        extract_id            = phases.get("extract", ""),
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
        phases                = phases,
    )
    return {
        "status":    "queued",
        "phases":    phases,
        "video_ids": video_ids,
        "endpoint":  "/api/v1/ycs/admin/pipeline",
    }


@router.post("/channel")
async def get_channel_videos(payload: ChannelRequest) -> dict:
    """Extract all channel videos → ES (Celery). `max_results=0` fetches ALL videos."""
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
    """Extract all playlist videos → ES (Celery). `max_results=0` fetches ALL videos."""
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
