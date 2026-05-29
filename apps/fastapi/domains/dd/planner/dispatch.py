"""Planner dispatch — async runners shared by FastAPI in-process mode and
Celery worker mode.

Both modes execute the same async logic; only the *runtime location* differs.
The HTTP route (in-process mode) spawns these as detached asyncio tasks; the
Celery task (worker mode) calls them via asyncio.run().

Three runners:
  - run_planner_async(thread_id, slug, mode):
        fresh planner kickoff. Builds initial state, spawns main task +
        cancel watcher, awaits terminal status, patches checkpointer,
        emits SSE terminal event.
  - resume_planner_async(thread_id):
        resume from last checkpoint. Three sub-paths:
          1. standard ainvoke(None) resume
          2. catch-up: status=done but newer IMPLEMENTED nodes missing
          3. truly nothing to do
  - run_missing_nodes_async(thread_id, missing):
        the catch-up worker (path 2 of resume).

All three converge on the same lifecycle pattern: spawn graph task + cancel
watcher, await terminal, write status/error to checkpoint, emit SSE
"terminal" event. Returns a terminal dict.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

import redis.asyncio as redis_aio

from .cancel import _redis_url, clear_cancel, watcher as cancel_watcher
from .graph import IMPLEMENTED, NODE_REGISTRY, NODE_TO_FIELD, build_graph
from .progress import emit_progress


logger = logging.getLogger(__name__)


def _planner_timing_key(slug: str) -> str:
    """MinIO key for the persisted planner timing roll-up (total wall).
    Surfaced on the navbar so the total survives a refresh and shows for an
    already-finished planner run (hybrid live+persisted model)."""
    return f"planner/{slug}/planner-timing-latest.json"


async def _persist_planner_timing(slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        from ..ingestion.storage import get_storage
        await get_storage().write(
            _planner_timing_key(slug),
            json.dumps({
                "slug": slug,
                "total_wall_ms": int(total_wall_ms),
                "finished_ts": time.time(),
            }, indent=2),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: timing persist failed "
            f"({type(e).__name__}: {e})"
        )


async def _await_with_watcher(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
    t0: Optional[float] = None,
    slug: Optional[str] = None,
) -> dict:
    """Common terminal-status lifecycle: await the planner task, write
    terminal status to checkpoint, emit SSE terminal event, cancel the
    watcher. Returns the terminal dict {"thread_id", "status", "error"?}.

    When `t0` (monotonic start) is given, the total wall-clock is computed,
    carried IN the terminal event (so the navbar gets it before the SSE
    stream closes — a separate post-terminal event would be missed), and
    persisted (when `slug` is given) for the load/cached navbar path."""
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

    try:
        await graph.aupdate_state(config, terminal_patch)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: aupdate_state failed for terminal "
            f"patch {terminal_patch!r}: {type(e).__name__}: {e}"
        )

    total_wall_ms = (
        int((time.monotonic() - t0) * 1000) if t0 is not None else None
    )
    if total_wall_ms is not None and slug:
        await _persist_planner_timing(slug, total_wall_ms)

    # Clear the live-run registry (set by start_planner) — the run has ended,
    # so a later refresh must NOT reconnect to this finished thread. Derive
    # the slug from the thread_id (`docs-distiller/{slug}/{uuid}`) when the
    # caller didn't pass it (e.g. the resume path).
    reg_slug = slug or (
        thread_id.split("/")[1] if thread_id.count("/") >= 2 else None
    )
    if reg_slug:
        await _clear_active_run(reg_slug)

    await emit_progress(
        thread_id, "planner", "terminal",
        status=terminal_patch.get("status", "unknown"),
        error=terminal_patch.get("error"),
        total_wall_ms=total_wall_ms,
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
    }


async def _clear_active_run(slug: str) -> None:
    """Best-effort delete of the planner live-run registry key
    (`dd:planner:current:{slug}`)."""
    try:
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            await r.delete(f"dd:planner:current:{slug}")
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run clear failed "
            f"({type(e).__name__}: {e})"
        )


async def run_planner_async(
    thread_id: str,
    slug: str,
    mode: str = "llm",
) -> dict:
    """Fresh planner kickoff. Builds graph + initial state, spawns the
    LangGraph task + cancel watcher, awaits terminal.

    Returns terminal dict suitable for either an HTTP background-task
    callback or a Celery task return value."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

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

    # Total planner wall-clock (span, not sum — nodes fan out in parallel);
    # _await_with_watcher carries it IN the terminal event + persists it.
    t0 = time.monotonic()
    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id, t0=t0, slug=slug,
    )


def missing_implemented_nodes(state: dict) -> list[str]:
    """Return IMPLEMENTED node names whose primary output field is
    missing/empty in state. Used by resume's catch-up path to detect
    threads that completed BEFORE node N was added to IMPLEMENTED."""
    missing: list[str] = []
    for name in IMPLEMENTED:
        field = NODE_TO_FIELD.get(name)
        if not field:
            continue
        val = state.get(field)
        if val is None or val == "" or val == []:
            missing.append(name)
    return missing


async def run_missing_nodes_async(
    thread_id: str,
    missing: list[str],
) -> dict:
    """Catch-up worker — invokes each missing IMPLEMENTED node via
    NODE_REGISTRY directly and patches state. Used when a thread reached
    END BEFORE a new IMPLEMENTED node was added — LangGraph's ainvoke(None)
    would short-circuit because the old checkpoint already consumed END."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    terminal_patch: dict = {"status": "done"}
    try:
        for name in missing:
            node_fn = NODE_REGISTRY.get(name)
            if node_fn is None:
                continue
            snap = await graph.aget_state(config)
            state = dict(snap.values or {})
            state["thread_id"] = thread_id
            result = await node_fn(state)
            if not isinstance(result, dict):
                continue
            await graph.aupdate_state(config, result)
            logger.info(
                f"[planner] {thread_id}: catch-up ran missing node "
                f"{name!r} → fields {sorted(result.keys())}"
            )
    except Exception as e:
        terminal_patch = {"status": "failed",
                          "error": f"{type(e).__name__}: {e}"}
        logger.exception(
            f"[planner] {thread_id}: catch-up failed mid-run "
            f"({type(e).__name__}: {e})"
        )

    try:
        await graph.aupdate_state(config, terminal_patch)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: aupdate_state failed for catch-up "
            f"terminal patch {terminal_patch!r}: {type(e).__name__}: {e}"
        )

    await emit_progress(
        thread_id, "planner", "terminal",
        status=terminal_patch.get("status", "unknown"),
        error=terminal_patch.get("error"),
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
    }


async def resume_planner_async(thread_id: str) -> dict:
    """Resume from last checkpoint. Three sub-paths handled inline; the
    caller (FastAPI route OR Celery task) gets a terminal dict."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snap = await graph.aget_state(config)
    if snap.values == {}:
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": (
                f"no checkpoints found for thread_id={thread_id!r}; "
                f"call POST /planner/{{slug}} to start a fresh run"
            ),
        }

    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    state = dict(snap.values or {})
    if state.get("status") == "done":
        missing = missing_implemented_nodes(state)
        if missing:
            await emit_progress(
                thread_id, "planner", "catch_up",
                missing=missing,
            )
            try:
                await graph.aupdate_state(config, {"status": "running"})
            except Exception as e:
                logger.warning(
                    f"[planner] {thread_id}: pre-catch-up status reset "
                    f"failed: {type(e).__name__}: {e}"
                )
            return await run_missing_nodes_async(thread_id, missing)
        # truly nothing to do
        await emit_progress(
            thread_id, "planner", "terminal",
            status="done", error=None,
        )
        return {
            "thread_id": thread_id,
            "status": "done",
            "error": None,
        }

    # standard LangGraph resume
    await emit_progress(
        thread_id, "planner", "resumed",
        next_nodes=list(snap.next or []),
    )
    main_task = asyncio.create_task(graph.ainvoke(None, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
    )
