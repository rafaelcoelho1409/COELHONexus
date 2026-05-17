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
import logging
import time
import uuid

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

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
    """Kick off a planner run for `slug`. Spawns the graph as an
    asyncio.Task with a cancel watcher beside it so POST /cancel can
    interrupt mid-flight. Returns thread_id + final state.

    If the caller supplies `thread_id`, it's used as-is — this lets the
    UI generate a UUID client-side BEFORE the POST returns, so the
    Cancel button + the polling loop both have a real thread_id from
    click 1 (no more 'pending' dead-zone)."""
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

    t0 = time.monotonic()
    # Spawn graph + watcher as concurrent tasks. Watcher polls Redis
    # cancel flag every 1s; on detection it cancels the graph task,
    # which raises CancelledError that we catch below.
    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    status = "done"
    final_state: dict = {}
    error: str | None = None
    try:
        final_state = await main_task
    except asyncio.CancelledError:
        status = "cancelled"
        logger.info(f"[planner] {thread_id}: cancelled by user")
        # Pull whatever state was checkpointed so the UI can show progress
        # up to the cancel point.
        try:
            snap = await graph.aget_state(config)
            final_state = dict(snap.values) if snap.values else {}
        except Exception:
            final_state = {}
    except Exception as e:
        status = "failed"
        error = f"{type(e).__name__}: {e}"
        logger.exception(f"[planner] {thread_id}: run failed")
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass

    final_state["status"] = status
    if error:
        final_state["error"] = error
    return {
        "thread_id":  thread_id,
        "slug":       slug,
        "mode":       mode,
        "status":     status,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "state":      final_state,
    }


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
