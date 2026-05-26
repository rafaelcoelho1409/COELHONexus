"""Planner endpoints — kick off + cancel + per-thread debug.

The graph itself runs in a Celery worker (queue `planner-{env}`); FastAPI
is purely the HTTP/SSE layer. Both POST routes dispatch to Celery via
`.delay()` and return immediately. SSE/cancel/checkpoint all work cross-
process: Redis pub/sub for events, Redis flag for cancel, Postgres for
LangGraph checkpoints. The shared async runners live in
`domains/dd/planner/dispatch.py`; the Celery task wrappers in
`domains/dd/planner/task.py`.

Migrated 2026-05-24 — see commit history for the prior in-process path.

  POST /planner/{slug}?mode=llm
      → dispatches `run_planner` Celery task. Returns immediately with
        `thread_id` + `status="queued"` + `celery_task_id`. The UI polls
        /debug/graph/{thread_id}/state + subscribes to /events for live
        progress.

  POST /planner/{thread_id}/resume
      → dispatches `resume_planner` Celery task. Handles all three resume
        paths (standard / catch-up missing nodes / no-op) inside the
        worker via `dispatch.resume_planner_async`.

  POST /planner/{thread_id}/cancel
      → sets the cancel flag in Redis. The watcher running inside the
        Celery worker's async runner picks it up within ~1s and cancels
        the graph (propagates LangGraph's CancelledError).

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

from domains.dd.planner.cancel import (
    _redis_url,
    clear_cancel,
    request_cancel,
)
from domains.dd.planner.graph import (
    IMPLEMENTED,
    NODE_ORDER,
    build_graph,
)
from domains.dd.planner.progress import subscribe_progress
from domains.dd.planner.task import (
    resume_planner as resume_planner_task,
    run_planner as run_planner_task,
)


logger = logging.getLogger(__name__)


router = APIRouter()


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
    """Kick off a planner run for `slug` by dispatching the `run_planner`
    Celery task. Returns IMMEDIATELY with `thread_id` + `status="queued"` —
    the UI polls /debug/graph/{thread_id}/state for progress and subscribes
    to /events for live SSE updates.

    If the caller supplies `thread_id`, it's used as-is — the UI generates
    a UUID client-side BEFORE the POST returns, so the Cancel button +
    the polling loop both have a real thread_id from click 1 (no 'pending'
    dead-zone).

    The graph itself executes inside a Celery worker (queue `planner-{env}`)
    via `dispatch.run_planner_async`. SSE/cancel/checkpoint all work cross-
    process via Redis pub/sub + Redis flag + Postgres respectively."""
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    if not thread_id:
        thread_id = f"docs-distiller/{slug}/{uuid.uuid4()}"

    # Clear stale cancel flag pre-dispatch (cheap defense-in-depth —
    # thread_id is fresh uuid so collision impossible, but explicit).
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    try:
        async_result = run_planner_task.delay(thread_id, slug, mode)
    except Exception as e:
        logger.exception(
            f"[planner] {thread_id}: celery dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "slug":           slug,
        "mode":           mode,
        "status":         "queued",
        "celery_task_id": async_result.id,
        "latency_ms":     0,
    }


@router.post("/{thread_id:path}/resume")
async def resume_planner(thread_id: str) -> dict:
    """Resume a planner run from its last LangGraph checkpoint by
    dispatching the `resume_planner` Celery task.

    Used by the FastHTML page-refresh recovery: when a worker pod restart
    kills the in-flight task, the state.status stays "running" but no live
    events arrive. The UI detects the orphan (no SSE events in ~5s) and
    POSTs here to continue from where it left off.

    Three execution paths handled inside `dispatch.resume_planner_async`:
      1. `status` ∈ {"running", "failed"} → standard `ainvoke(None)`
         resume from the last committed checkpoint.
      2. `status == "done"` BUT some IMPLEMENTED nodes have no output
         field in state → catch-up path. This happens when a new node
         was added to IMPLEMENTED AFTER the thread completed; the worker
         runs each missing node directly via NODE_REGISTRY + aupdate_state,
         preserving SSE events naturally.
      3. `status == "done"` AND no missing nodes → terminal "done" event
         with no work performed.
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    try:
        async_result = resume_planner_task.delay(thread_id)
    except Exception as e:
        logger.exception(
            f"[planner] {thread_id}: celery resume dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "status":         "queued",
        "celery_task_id": async_result.id,
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

    from domains.dd.ingestion.storage import get_storage

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

    # 2026-05-26 — Heartbeat every _SSE_HEARTBEAT_S keeps idle SSE
    # connections alive through k3d/traefik/nginx-class proxies (default
    # idle-stream timeout 60s). Planner has long gaps during off_topic
    # bandit cascade + cluster UMAP warm-up; without this the TCP
    # connection gets dropped and the UI loses events emitted in the
    # down-window. SSE comments (`:`) are ignored by clients but their
    # bytes flow through TCP.
    _SSE_HEARTBEAT_S = 15.0
    _DONE = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in subscribe_progress(thread_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(
                    f"[planner-events] {thread_id}: pump crashed "
                    f"({type(e).__name__}: {e})"
                )
            finally:
                await queue.put(_DONE)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_S,
                    )
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is _DONE:
                    return
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

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
