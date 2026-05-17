"""Planner endpoints — kick off + cancel + per-thread debug.

  POST /planner/{slug}?mode=llm
      → starts a planner run for `slug`. `mode` ∈ {llm, classical} —
        only "llm" is wired today; "classical" is reserved for the
        future classical+LLM mode (numpy community_detection + KeyLLM).
        Returns the `thread_id` used as the LangGraph checkpoint group
        key + the LangFuse session id. Each substep writes one
        checkpoint row + one OTel span.

  POST /planner/{thread_id}/cancel
      → sets the cancel flag in Redis. The cancel watcher running
        alongside the planner task picks it up within ~1s and cancels
        the main task, which propagates LangGraph's CancelledError.
        The POST /planner/{slug} call returns with status="cancelled".

  GET /planner/debug/graph/{thread_id}/state
      → current state for the thread (latest checkpoint).

  GET /planner/debug/graph/{thread_id}/history
      → every checkpoint (super-step) in the thread, newest first.

Replay + fork endpoints (POST /replay, POST /edit) ship later once
the per-checkpoint replay UX is wired.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from services.docs_distiller.planner.cancel import (
    _redis_url,
    clear_cancel,
    request_cancel,
    watcher as cancel_watcher,
)
from services.docs_distiller.planner.graph import (
    IMPLEMENTED,
    NODE_ORDER,
    build_graph,
)
from services.docs_distiller.planner.progress import (
    emit_progress,
    subscribe_progress,
)


logger = logging.getLogger(__name__)


router = APIRouter()


# Strong refs to detached planner tasks so the event loop doesn't garbage-
# collect them mid-run. Each task removes itself on completion via a
# done_callback (see _spawn_planner_task below).
_active_runs: set[asyncio.Task] = set()


async def _run_planner_background(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
) -> None:
    """Await the already-spawned planner graph task in the background,
    handling cancel / error / success identically to the old in-route
    logic. Writes terminal `status` ("done" / "cancelled" / "failed") +
    optional `error` back into the LangGraph checkpointer via
    aupdate_state so the polling UI can detect run completion via the
    /debug/graph/{thread_id}/state response."""
    terminal_patch: dict = {}
    try:
        await main_task
        terminal_patch["status"] = "done"
        logger.info(f"[planner] {thread_id}: done")
    except asyncio.CancelledError:
        terminal_patch["status"] = "cancelled"
        logger.info(f"[planner] {thread_id}: cancelled by user")
    except Exception as e:
        terminal_patch["status"] = "failed"
        terminal_patch["error"] = f"{type(e).__name__}: {e}"
        logger.exception(
            f"[planner] {thread_id}: run failed ({type(e).__name__}: {e})"
        )
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass

    # Patch the terminal status into the checkpointer so callers of
    # /debug/graph/{thread_id}/state see the run is over (used as the
    # state-snapshot endpoint by the UI on reload).
    try:
        await graph.aupdate_state(config, terminal_patch)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: aupdate_state failed for terminal "
            f"patch {terminal_patch!r}: {type(e).__name__}: {e}"
        )

    # Emit a terminal event on the SSE channel so the live-UI listener
    # can detect run completion without polling. Best-effort; the
    # state-patch above is the authoritative record.
    await emit_progress(
        thread_id, "planner", "terminal",
        status=terminal_patch.get("status", "unknown"),
        error=terminal_patch.get("error"),
    )


@router.get("/info")
async def planner_info() -> dict:
    """Catalog of planner substeps + which are wired into the runtime
    graph. The UI uses this to mark unimplemented cards as "future" and
    to suppress the misleading "running" indicator on substeps that the
    runtime will silently skip."""
    return {
        "node_order": list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        # Modes the planner CAN run in. The UI surfaces this so it can
        # render an enabled/disabled toggle. Today only "llm" is wired.
        "modes": [
            {"key": "llm",       "label": "LLM-only",        "enabled": True},
            {"key": "classical", "label": "Classical + LLM", "enabled": False},
        ],
    }


_VALID_MODES = {"llm", "classical"}


@router.post("/{slug}")
async def start_planner(
    slug: str, mode: str = "llm", thread_id: str | None = None,
) -> dict:
    """Kick off a planner run for `slug`. Spawns the graph in a detached
    background task and returns IMMEDIATELY with `thread_id` + status
    "running" — the UI polls /debug/graph/{thread_id}/state for progress.

    The previous synchronous-await design held the HTTP connection open
    for the full graph wall (160s+ for cold embed_corpus on a 777-doc
    corpus), which blew past the FastHTML proxy's httpx timeout and
    surfaced as a 500 to the user even though the backend succeeded.

    If the caller supplies `thread_id`, it's used as-is — the UI
    generates a UUID client-side BEFORE the POST returns, so the
    Cancel button + the polling loop both have a real thread_id from
    click 1 (no 'pending' dead-zone)."""
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    if not thread_id:
        thread_id = f"docs-distiller/{slug}/{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = build_graph()
    except RuntimeError as e:
        # AsyncPostgresSaver not initialized — lifespan startup failure.
        raise HTTPException(status_code=503, detail=str(e))

    # Clear any stale cancel flag (defense-in-depth — thread_id is fresh
    # uuid so collision impossible, but cheap to be explicit).
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    initial_state = {
        "framework_slug": slug,
        "thread_id": thread_id,
        "planner_mode": mode,
        "status": "running",
    }

    # Spawn the graph as a DETACHED background task. The watcher (cancel
    # flag poller) runs beside it. The wrapper coroutine owns lifecycle
    # cleanup (catch + watcher cancel) so this route can return
    # immediately without holding the HTTP connection open for the full
    # graph wall. Strong-ref via _active_runs so the event loop doesn't
    # GC the task mid-run.
    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    bg_task = asyncio.create_task(
        _run_planner_background(graph, config, main_task, watcher_task, thread_id)
    )
    _active_runs.add(bg_task)
    bg_task.add_done_callback(_active_runs.discard)

    return {
        "thread_id":  thread_id,
        "slug":       slug,
        "mode":       mode,
        "status":     "running",
        "latency_ms": 0,
    }


@router.post("/{thread_id:path}/resume")
async def resume_planner(thread_id: str) -> dict:
    """Resume a planner run from its last LangGraph checkpoint.

    Used by the FastHTML page-refresh recovery: when a pod restart kills
    the in-flight asyncio bg task, the state.status stays "running" but
    no live events arrive. The UI detects the orphan (no SSE events in
    ~5s) and POSTs here to continue from where it left off.

    LangGraph treats `ainvoke(None, config)` as "advance from the last
    committed checkpoint" — completed nodes are NOT re-run; only the
    next-pending node onward executes. This means: corpus_load +
    embed_corpus (already checkpointed) are skipped automatically, and
    off_topic restarts from the start of THAT node (LangGraph doesn't
    do mid-node resume — that requires interrupt+resume tooling we
    haven't wired)."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}

    # Sanity-check that the thread has any checkpoint at all (so we
    # don't spawn a no-op run on a typo'd thread_id).
    snap = await graph.aget_state(config)
    if snap.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints found for thread_id={thread_id!r}; "
                   f"call POST /planner/{{slug}} to start a fresh run",
        )

    # Clear any stale cancel flag from the prior incarnation.
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    await emit_progress(
        thread_id, "planner", "resumed",
        next_nodes=list(snap.next or []),
    )

    # Re-spawn the graph + watcher + background wrapper. Pass `None` as
    # state so LangGraph picks up from the last checkpoint instead of
    # restarting from scratch.
    main_task = asyncio.create_task(graph.ainvoke(None, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    bg_task = asyncio.create_task(
        _run_planner_background(graph, config, main_task, watcher_task, thread_id)
    )
    _active_runs.add(bg_task)
    bg_task.add_done_callback(_active_runs.discard)

    return {
        "thread_id":  thread_id,
        "status":     "resuming",
        "next_nodes": list(snap.next or []),
    }


@router.get("/recent")
async def list_recent_planners() -> dict:
    """List the most recent thread per framework slug. Used by the
    FastHTML page-refresh recovery on browsers that wipe localStorage
    between sessions (Brave private mode, mobile Safari, etc.) — when
    no client-side hint exists, the JS falls back to this endpoint to
    discover which slugs have cached LangGraph state.

    thread_id format is `docs-distiller/{slug}/{uuid}` so split_part
    extracts the slug. DISTINCT ON returns one row per slug — the one
    with the latest checkpoint_id (LangGraph uses UUIDv6, sortable)."""
    import os
    from urllib.parse import quote
    import psycopg

    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get(
        "POSTGRES_DATABASE", os.environ.get("POSTGRES_DB", "postgres"),
    )
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    # Per-slug thread selection: prefer the thread with the MOST
    # checkpoints (proxy for "ran longest = most node-fields committed").
    # Sorting only by checkpoint_id (timestamp) was picking abandoned
    # threads that died after corpus_load over earlier threads that
    # successfully completed all 3 nodes.
    out: list[dict] = []
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    WITH thread_stats AS (
                        SELECT
                            split_part(thread_id, '/', 2) AS slug,
                            thread_id,
                            count(*)             AS ckpt_count,
                            max(checkpoint_id)   AS latest_ckpt
                        FROM checkpoints
                        WHERE thread_id LIKE 'docs-distiller/%'
                        GROUP BY thread_id
                    )
                    SELECT DISTINCT ON (slug)
                        slug, thread_id, ckpt_count, latest_ckpt
                    FROM thread_stats
                    ORDER BY slug, ckpt_count DESC, latest_ckpt DESC
                """)
                for slug, tid, ckpt_count, latest in await cur.fetchall():
                    out.append({
                        "slug":          slug,
                        "thread_id":     tid,
                        "checkpoint_id": str(latest),
                        "ckpt_count":    int(ckpt_count),
                    })
    except Exception as e:
        logger.warning(f"[planner-recent] query failed: {e}")
    return {"recent": out}


@router.delete("/{slug}/wipe")
async def wipe_planner(slug: str) -> dict:
    """Delete ALL planner state for `slug`: MinIO embedding blobs (under
    planner/{slug}/) AND Postgres LangGraph checkpoints (across every
    thread_id matching docs-distiller/{slug}/%).

    Browser-side cache (`dd:planner:active:{slug}` in localStorage) is
    the caller's responsibility — the JS wipePlanner() helper does that
    in the same call. Returns deletion counts for both stores."""
    import os
    from urllib.parse import quote
    import psycopg

    from services.docs_distiller.ingestion.storage_minio import get_storage

    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    # 1. MinIO embeddings cache
    minio = get_storage()
    try:
        n_minio = await minio.delete_prefix(f"planner/{slug}/")
    except Exception as e:
        logger.warning(f"[planner-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    # 2. Postgres LangGraph checkpoints — direct query; LangGraph
    #    doesn't expose a delete-thread API.
    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "coelhonexus")
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    counts: dict = {}
    pattern = f"docs-distiller/{slug}/%"
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True,
        ) as conn:
            # Order matters: write-side tables FIRST (they may FK to checkpoints).
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                async with conn.cursor() as cur:
                    try:
                        await cur.execute(
                            f"DELETE FROM {tbl} WHERE thread_id LIKE %s",
                            (pattern,),
                        )
                        counts[tbl] = cur.rowcount
                    except Exception as e:
                        counts[tbl] = f"skipped: {type(e).__name__}: {e}"
    except Exception as e:
        logger.warning(f"[planner-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    logger.info(
        f"[planner-wipe] {slug}: minio={n_minio} blobs, postgres={counts}"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
    }


@router.get("/{thread_id:path}/events")
async def planner_events(thread_id: str) -> StreamingResponse:
    """Server-Sent Events stream of mid-node progress for `thread_id`.

    Backed by a Redis pub/sub channel that each planner node publishes
    to during its run (`services/docs_distiller/planner/progress.py`).
    Subscribers also receive a catch-up replay of any events that landed
    BEFORE they connected (from a per-thread snapshot list), so late
    subscribers don't miss the early "start" event.

    Event format: `data: <json>\\n\\n` (text/event-stream). Each JSON
    object carries at minimum `step`, `kind`, `ts`; per-event extras
    vary by step (e.g. embed_corpus emits `chunks_done`/`chunks_total`
    in its `batch` events).

    The connection stays open until the client disconnects OR the
    planner emits a `done` event for the final node + we observe it +
    the client closes the EventSource. Server doesn't close the stream
    on its own — clients close after seeing terminal status."""

    async def _gen():
        # Initial comment-line keeps proxies happy + flushes headers.
        yield b": stream open\n\n"
        try:
            async for event in subscribe_progress(thread_id):
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache, no-transform",
            "Connection":        "keep-alive",
            # nginx-style proxies sometimes buffer event-stream by default;
            # this header tells them to flush as it arrives.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{thread_id:path}/cancel")
async def cancel_planner(thread_id: str) -> dict:
    """Set the cancel flag for `thread_id`. The watcher running beside
    the planner task picks it up within ~1s and cancels the graph,
    returning status='cancelled' from POST /planner/{slug}.

    Path uses `:path` to allow the slug-prefixed thread_id structure
    (`docs-distiller/{slug}/{uuid}`) without URL-encoding."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await request_cancel(r, thread_id)
    finally:
        await r.aclose()
    return {"thread_id": thread_id, "status": "cancel_requested"}


@router.get("/debug/graph/{thread_id:path}/state")
async def get_graph_state(thread_id: str) -> dict:
    """Latest checkpoint for `thread_id`. 404 if no checkpoints exist."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints found for thread_id={thread_id!r}",
        )
    return {
        "thread_id": thread_id,
        "next_nodes": list(snapshot.next or []),
        "values": snapshot.values,
        "config": snapshot.config,
        "metadata": snapshot.metadata,
    }


@router.get("/debug/graph/{thread_id:path}/history")
async def get_graph_history(thread_id: str) -> dict:
    """Every checkpoint for `thread_id`, newest first."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}
    history = []
    async for snap in graph.aget_state_history(config):
        history.append({
            "checkpoint_id": (snap.config or {}).get(
                "configurable", {},
            ).get("checkpoint_id"),
            "next_nodes": list(snap.next or []),
            "values": snap.values,
            "metadata": snap.metadata,
        })
    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints found for thread_id={thread_id!r}",
        )
    return {"thread_id": thread_id, "count": len(history), "checkpoints": history}
