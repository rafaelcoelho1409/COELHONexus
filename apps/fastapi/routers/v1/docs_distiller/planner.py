"""Planner endpoints — kick off + per-thread debug.

  POST /planner/{slug}
      → starts a planner run for `slug`. Returns the `thread_id` used
        as the LangGraph checkpoint group key + the LangFuse session id.
        Each substep writes one checkpoint row + one OTel span.

  GET /planner/debug/graph/{thread_id}/state
      → current state for the thread (latest checkpoint).

  GET /planner/debug/graph/{thread_id}/history
      → every checkpoint (super-step) in the thread, newest first.

Replay + fork endpoints (POST /replay, POST /edit) ship in step 2 once
the substep logic actually does something worth re-running.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException

from services.docs_distiller.planner.graph import build_graph


router = APIRouter()


@router.post("/{slug}")
async def start_planner(slug: str) -> dict:
    """Kick off a planner run for `slug`. Returns thread_id + final state."""
    thread_id = f"docs-distiller/{slug}/{uuid.uuid4()}"
    config = {"configurable": {"thread_id": thread_id}}

    try:
        graph = build_graph()
    except RuntimeError as e:
        # AsyncPostgresSaver not initialized — lifespan startup failure.
        raise HTTPException(status_code=503, detail=str(e))

    t0 = time.monotonic()
    try:
        final_state = await graph.ainvoke(
            {"framework_slug": slug, "thread_id": thread_id, "status": "running"},
            config,
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"planner run failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id": thread_id,
        "slug": slug,
        "latency_ms": int((time.monotonic() - t0) * 1000),
        "state": final_state,
    }


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
