"""ycs/content — sync yt-dlp search + Celery dispatchers for video/channel/playlist ingestion.
yt-dlp errors translate to 502 (subprocess failure) / 504 (timeout)."""
from __future__ import annotations
import domains
from . import service

from fastapi import APIRouter, HTTPException, Request
from opentelemetry import trace


router = APIRouter()


@router.post("/search", response_model = domains.ycs.content.schemas.SearchResponse)
async def search_videos(payload: domains.ycs.content.schemas.SearchRequest) -> domains.ycs.content.schemas.SearchResponse:
    """Synchronous yt-dlp `ytsearch*` — returns snippets, no persistence."""
    svc = domains.ycs.content.service.get_search_service()
    try:
        return await svc.search(payload)
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except domains.ycs.content.errors.YtDlpJsonParseError as e:
        raise HTTPException(
            status_code = 502, detail = f"yt-dlp output not JSON: {e}",
        )


@router.post("/videos")
async def get_videos(payload: domains.ycs.extract.schemas.VideosRequest) -> dict:
    """Extract specific videos → ES (Celery). Bare extract only; the Videos tab uses `/videos/pipeline`.

    2026-09-15: "bare" is about the API SHAPE, not behavior — `extract_videos`
    (the Celery task, shared verbatim with `/videos/pipeline`) always uses
    its own task id as `extract_id` and self-dispatches per-video Qdrant/
    Neo4j streaming work once `include_transcription=True`, regardless of
    which endpoint fired it. Needs the same gate as every other entry
    point that can write to Qdrant."""
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, detail = "video_ids is required",
        )
    await service._raise_if_embedding_migration_needed(payload.include_transcription)
    import domains.ycs.extract.task
    task = domains.ycs.extract.task.extract_videos.delay(
        payload.video_ids,
        payload.include_transcription,
        payload.transcription_languages,
    )
    trace.get_current_span().set_attribute("celery.task_id", task.id)
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.post("/videos/pipeline")
async def get_videos_pipeline(
    payload: domains.ycs.extract.schemas.VideosRequest, request: Request,
) -> dict:
    """Full 3-phase pipeline (extract → Qdrant → Neo4j → invalidate). Chained at the API layer so
    each phase reports its own progress. Snapshots params to Redis to enable Rerun."""
    if not payload.video_ids:
        raise HTTPException(
            status_code = 400, detail = "video_ids is required",
        )
    await service._raise_if_embedding_migration_needed(payload.include_transcription)
    phases = domains.ycs.pipeline_task.service.dispatch_videos_pipeline(
        video_ids             = payload.video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await domains.ycs.pipeline_task.service.persist_pipeline_state(
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
    state = await domains.ycs.pipeline_task.service.load_pipeline_state(
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
    await service._raise_if_embedding_migration_needed(state.get("include_transcription", True))
    phases = domains.ycs.pipeline_task.service.dispatch_videos_pipeline(
        video_ids             = state["video_ids"],
        include_transcription = state.get("include_transcription", True),
        languages             = state.get("languages"),
    )
    await domains.ycs.pipeline_task.service.persist_pipeline_state(
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
    state = await domains.ycs.pipeline_task.service.load_pipeline_state(
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
    `__Entity__` nodes left intact — may be shared across other videos.

    Sets the cooperative-cancel flag too (best-effort — a task already
    past its next checkpoint won't see it before the hard revoke below
    lands anyway), but keeps `terminate=True`: unlike Stop, Wipe has a
    correctness requirement (no orphan writes after data is deleted)
    that cooperative-only cancellation can't guarantee — a task stuck
    mid-LLM-call between checkpoints would otherwise finish and write
    after the wipe already ran."""
    redis = getattr(request.app.state, "redis_aio", None)
    state = await domains.ycs.pipeline_task.service.load_pipeline_state(redis, extract_id)
    if not state or not state.get("video_ids"):
        raise HTTPException(
            status_code = 404,
            detail      = (
                f"No saved pipeline state for {extract_id} — already "
                f"expired (24h TTL) or unknown."
            ),
        )
    if redis is not None:
        await domains.ycs.pipeline_task.service.request_cancel(redis, extract_id)
    summary = await domains.ycs.pipeline_task.service.wipe_videos_data(
        video_ids   = state["video_ids"],
        neo4j_graph = getattr(request.app.state, "neo4j_graph", None),
    )
    # `extract` is the only real static task id left — the per-video
    # streaming fan-out means Neo4j/Qdrant have no fixed ids to revoke;
    # `get_dispatched_task_ids` reads back every per-video task id
    # `extract/task.py` recorded as it fired them (best-effort tracking
    # — see that module's `_on_video_indexed`).
    phases: dict[str, str] = state.get("phases", {})
    phase_ids = [phases.get("extract", ""), phases.get("invalidate", "")]
    phase_ids.extend(
        await domains.ycs.pipeline_task.service.get_dispatched_task_ids(redis, extract_id) if redis else [],
    )
    revoke_outcomes = domains.ycs.pipeline_task.service.revoke_pipeline_phases(phase_ids, terminate = True)
    return {
        "status":          "wiped",
        "summary":         summary,
        "revoke_outcomes": revoke_outcomes,
    }


@router.post("/videos/pipeline/{extract_id}/stop")
async def stop_videos_pipeline(extract_id: str, request: Request) -> dict:
    """Cooperatively cancel `extract_id`: sets a Redis flag `domains.ycs.extract.task.extract_videos`'
    Playwright chunk loop and `ingest_to_neo4j`'s retry-pass loop poll at safe
    checkpoints, then revokes (non-terminating) any phase/per-video task still
    queued but not yet started. Preserves SUCCESS-state phases; idempotent
    Qdrant upserts and Neo4j skip-on-video_id let a rerun pick up cleanly.

    2026-09-14: no longer sends SIGTERM to already-running tasks — that
    was observed live to wedge Celery's prefork pool (see
    `pipeline_task.service.revoke_pipeline_phases` docstring for the
    full incident). A task already past its last checkpoint when Stop
    is clicked finishes that unit of work (one Playwright chunk of up
    to 10 videos, or one Neo4j retry pass) before noticing the flag —
    bounded, not indefinite."""
    redis = getattr(request.app.state, "redis_aio", None)
    state = await domains.ycs.pipeline_task.service.load_pipeline_state(redis, extract_id)
    if not state or not state.get("phases"):
        raise HTTPException(
            status_code = 404,
            detail      = (
                f"No saved pipeline state for {extract_id} — already "
                f"expired (24h TTL) or unknown."
            ),
        )
    if redis is not None:
        await domains.ycs.pipeline_task.service.request_cancel(redis, extract_id)
    phases: dict[str, str] = state["phases"]
    phase_ids = [phases.get("extract", ""), phases.get("invalidate", "")]
    phase_ids.extend(
        await domains.ycs.pipeline_task.service.get_dispatched_task_ids(redis, extract_id) if redis else [],
    )
    outcomes = domains.ycs.pipeline_task.service.revoke_pipeline_phases(phase_ids, terminate = False)
    return {
        "status":   "cancel_requested",
        "phases":   phases,
        "outcomes": outcomes,
    }


@router.get("/videos/preview", response_model = domains.ycs.content.schemas.EnumerationResponse)
async def preview_videos(
    ids:    str,
    limit:  int = 100,
    offset: int = 0,
) -> domains.ycs.content.schemas.EnumerationResponse:
    """yt-dlp metadata fetch for a comma-separated `ids=` list. Same `domains.ycs.content.schemas.EnumerationResponse` shape
    as channel/playlist so picker.js renders all three tabs with one shared module."""
    video_ids = [v.strip() for v in (ids or "").split(",") if v.strip()]
    if not video_ids:
        raise HTTPException(
            status_code = 400, detail = "ids is required (comma-separated)",
        )
    svc = domains.ycs.content.service.get_search_service()
    try:
        return await svc.preview_videos(
            video_ids = video_ids, limit = limit, offset = offset,
        )
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except domains.ycs.content.errors.YtDlpJsonParseError as e:
        raise HTTPException(
            status_code = 502, detail = f"yt-dlp output not JSON: {e}",
        )


@router.get("/channel/videos", response_model = domains.ycs.content.schemas.EnumerationResponse)
async def enumerate_channel_videos(
    id:     str,
    limit:  int = 100,
    offset: int = 0,
) -> domains.ycs.content.schemas.EnumerationResponse:
    """Paginated channel video listing. Resolves any input shape (bare `UC…`, `@handle`, URL) to
    the uploads playlist for cheapest pagination. `total=None` when yt-dlp can't surface `playlist_count`."""
    svc = domains.ycs.content.service.get_search_service()
    try:
        return await svc.enumerate_videos(
            source = "channel", raw_input = id,
            limit  = limit, offset = offset,
        )
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except domains.ycs.content.errors.YtDlpJsonParseError as e:
        raise HTTPException(
            status_code = 502, detail = f"yt-dlp output not JSON: {e}",
        )
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))


@router.get("/playlist/videos", response_model = domains.ycs.content.schemas.EnumerationResponse)
async def enumerate_playlist_videos(
    id:     str,
    limit:  int = 100,
    offset: int = 0,
) -> domains.ycs.content.schemas.EnumerationResponse:
    """Paginated playlist video listing. Accepts bare `PL…`/`UU…`, full `playlist?list=…`, or `watch?v=…&list=…` URLs."""
    svc = domains.ycs.content.service.get_search_service()
    try:
        return await svc.enumerate_videos(
            source = "playlist", raw_input = id,
            limit  = limit, offset = offset,
        )
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
        raise HTTPException(
            status_code = 502,
            detail = f"yt-dlp returncode={e.returncode}: {e.stderr[:400]}",
        )
    except domains.ycs.content.errors.YtDlpJsonParseError as e:
        raise HTTPException(
            status_code = 502, detail = f"yt-dlp output not JSON: {e}",
        )
    except ValueError as e:
        raise HTTPException(status_code = 400, detail = str(e))


@router.post("/channel/pipeline")
async def channel_pipeline(
    payload: domains.ycs.extract.schemas.ChannelPipelineRequest, request: Request,
) -> dict:
    """Enumerate ALL channel videos server-side, then dispatch the 3-phase pipeline. Bypasses the 100-per-page picker cap."""
    svc = domains.ycs.content.service.get_search_service()
    try:
        video_ids = await svc.enumerate_all_video_ids(
            source = "channel", raw_input = payload.channel_id,
        )
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
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
    await service._raise_if_embedding_migration_needed(payload.include_transcription)
    phases = domains.ycs.pipeline_task.service.dispatch_videos_pipeline(
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await domains.ycs.pipeline_task.service.persist_pipeline_state(
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
    payload: domains.ycs.extract.schemas.PlaylistPipelineRequest, request: Request,
) -> dict:
    """Enumerate ALL playlist videos server-side, then dispatch the 3-phase pipeline."""
    svc = domains.ycs.content.service.get_search_service()
    try:
        video_ids = await svc.enumerate_all_video_ids(
            source = "playlist", raw_input = payload.playlist_id,
        )
    except domains.ycs.content.errors.YtDlpTimeoutError as e:
        raise HTTPException(status_code = 504, detail = str(e))
    except domains.ycs.content.errors.YtDlpSubprocessError as e:
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
    await service._raise_if_embedding_migration_needed(payload.include_transcription)
    phases = domains.ycs.pipeline_task.service.dispatch_videos_pipeline(
        video_ids             = video_ids,
        include_transcription = payload.include_transcription,
        languages             = payload.transcription_languages,
    )
    await domains.ycs.pipeline_task.service.persist_pipeline_state(
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
async def get_channel_videos(payload: domains.ycs.extract.schemas.ChannelRequest) -> dict:
    """Extract all channel videos → ES (Celery). `max_results=0` fetches ALL videos."""
    import domains.ycs.extract.task
    task = domains.ycs.extract.task.extract_channel.delay(
        payload.channel_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    trace.get_current_span().set_attribute("celery.task_id", task.id)
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.post("/playlist")
async def get_playlist_videos(payload: domains.ycs.extract.schemas.PlaylistRequest) -> dict:
    """Extract all playlist videos → ES (Celery). `max_results=0` fetches ALL videos."""
    import domains.ycs.extract.task
    task = domains.ycs.extract.task.extract_playlist.delay(
        payload.playlist_id,
        payload.max_results,
        payload.include_transcription,
        payload.transcription_languages,
    )
    trace.get_current_span().set_attribute("celery.task_id", task.id)
    return {
        "task_id":  task.id,
        "status":   "queued",
        "endpoint": f"/api/v1/ycs/admin/task/{task.id}",
    }


@router.get("/embedding-migration/status")
async def embedding_migration_status(request: Request) -> dict:
    """Whether a migration is needed and/or already running — same check
    `_raise_if_embedding_migration_needed` uses, exposed read-only for the
    Settings/Ingestion page to show a banner before the user even tries
    to dispatch anything."""

    qdrant = service._build_qdrant()
    try:
        mismatch = await domains.ycs.embedding_migration.service.check_migration_needed(qdrant, domains.settings.embeddings.service.get_configured_model())
        active_collection = await domains.ycs.embedding_migration.service.get_active_collection_name(qdrant)
    finally:
        await qdrant.close()
    redis = getattr(request.app.state, "redis_aio", None)
    state = await domains.ycs.embedding_migration.service.get_migration_state(redis) if redis is not None else None
    return {
        "needed":            mismatch is not None,
        "mismatch":          mismatch,
        "state":             state,
        "active_collection": active_collection,
    }


@router.post("/embedding-migration/start")
async def embedding_migration_start(request: Request) -> dict:
    """Dispatch the re-embed job. 404 if nothing actually needs
    migrating (avoids a spurious re-embed if the user double-clicks
    after the gate already cleared)."""

    redis = getattr(request.app.state, "redis_aio", None)
    if redis is None:
        raise HTTPException(status_code = 503, detail = "Redis unavailable")
    qdrant = service._build_qdrant()
    try:
        to_model = domains.settings.embeddings.service.get_configured_model()
        mismatch = await domains.ycs.embedding_migration.service.check_migration_needed(qdrant, to_model)
        if mismatch is None:
            raise HTTPException(
                status_code = 404,
                detail = "No embedding-model mismatch detected — nothing to migrate.",
            )
        dimensions, _ = await domains.ycs.embeddings.service.get_embedding_info()
        state = await domains.ycs.embedding_migration.service.dispatch_migration(
            redis, qdrant,
            from_model = mismatch["from_model"], to_model = to_model, dimensions = dimensions,
        )
    finally:
        await qdrant.close()
    return {
        "status":   "queued",
        "state":    state,
        "endpoint": f"/api/v1/ycs/admin/task/{state.get('task_id', '')}",
    }
