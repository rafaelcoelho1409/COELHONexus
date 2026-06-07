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

from fastapi import APIRouter, HTTPException, Request

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

    Direct port of deprecated `routers/v1/youtube/content.py:L70-89`.
    Kept for back-compat / parity with channel + playlist; the live
    Videos tab now POSTs to `/videos/pipeline` (full 3-phase chain)."""
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
    """Full 3-phase ingest pipeline for a list of video IDs (Celery
    chain): `extract_videos → ingest_to_qdrant → ingest_to_neo4j →
    invalidate_cache`. Returns the chain's 3 user-visible task_ids
    immediately so the FastHTML Ingest page can render 3 live
    progress bars.

    Wave 5 polish — the Videos tab's `Start ingest` button now wires
    to this endpoint instead of the bare `/videos` extract. Qdrant +
    Neo4j ingestion are MANDATORY per the user spec; chaining at the
    API layer (vs. a single orchestrator task) lets each phase report
    its own progress via `self.update_state(...)`.

    Also snapshots the dispatch params (video_ids + flags) to Redis
    so the Ingest page's Rerun button can resurrect this run without
    making the user pick videos again."""
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
        "status": "queued",
        "phases": phases,
        "endpoint": "/api/v1/ycs/admin/pipeline",
    }


@router.post("/videos/pipeline/{extract_id}/rerun")
async def rerun_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Re-fire the 3-phase chain for a prior dispatch, looking up the
    original `{video_ids, include_transcription, languages}` from Redis
    (keyed by Phase A extract id, 24h TTL).

    Cache behavior on rerun (1:1 deprecated semantics):
      - Phase A: yt-dlp metadata re-fetches; transcript Playwright
        fetch SKIPS videos already in ES (`_check_existing_transcriptions`).
      - Phase B: Qdrant point ids are `md5(video_id_chunk_index)` so
        re-upserts overwrite in place. No skip — embedding work runs again.
      - Phase C: `extract_and_store_graph` queries Neo4j for already-
        tagged Document nodes and SKIPS those video_ids.
    So partial failures retry cleanly; full successes mostly no-op."""
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
        "status": "queued",
        "phases": phases,
        "rerun_of": extract_id,
    }


@router.post("/videos/pipeline/{extract_id}/stop")
async def stop_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Revoke every task in the Videos pipeline chain for `extract_id`
    (`SIGTERM` to the running task; revoke flag prevents queued ones
    from starting).

    Preserves successful work — any phase already in SUCCESS state
    keeps its ES / Qdrant / Neo4j writes (revoke is a no-op for
    terminal tasks). In-flight phases get cut at the next yield
    point; the data they had partially written stays put. Idempotent
    Qdrant upserts (md5 chunk ids) and the Phase-C skip-on-video_id
    check let a subsequent rerun pick up cleanly.

    Looks up the chain task_ids from Redis (`persist_pipeline_state`
    snapshots them on dispatch + rerun) so the client only needs to
    pass the extract_id."""
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
