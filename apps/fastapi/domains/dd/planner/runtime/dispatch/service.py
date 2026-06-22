"""Async orchestration: kickoff, resume, catch-up. Shared by FastAPI
in-process mode and the Celery worker — both modes execute the same async
logic; only the runtime location differs.

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
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import redis.asyncio as redis_aio

from infra.langfuse import (
    set_current_span_langfuse_io,
    set_current_span_langfuse_observation_metadata,
    set_current_span_langfuse_trace_metadata,
)
from infra.otel import get_tracer

from ....ingestion.storage import get_storage
from ..cancel import clear_cancel, watcher as cancel_watcher
from ...graph import NODE_REGISTRY, build_graph
from ...keys import active_run_key, planner_timing_key, redis_url
from ...params import REDIS_CONNECT_TIMEOUT_S, REDIS_OP_TIMEOUT_S
from ..observability import record_planner_run
from ..progress import emit_progress
from .domain import missing_implemented_nodes


logger = logging.getLogger(__name__)



PLANNER_THREAD_PREFIX = "docs-distiller"


def make_thread_id(slug: str) -> str:
    """Per-planner-run thread_id. Format: `docs-distiller/{slug}/{uuid}`.
    Matches the JS-pre-generated id format so the Cancel button has a real
    thread_id from click 1 (no 'pending' dead-zone)."""
    return f"{PLANNER_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"



async def _persist_planner_timing(slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        await get_storage().write(
            planner_timing_key(slug),
            json.dumps({
                "slug": slug,
                "total_wall_ms": int(total_wall_ms),
                "finished_ts": time.time(),
            }, indent = 2),
            content_type = "application/json",
        )
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: timing persist failed "
            f"({type(e).__name__}: {e})"
        )


async def _clear_active_run(slug: str) -> None:
    """Best-effort delete of the planner live-run registry key."""
    try:
        r = redis_aio.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout = REDIS_OP_TIMEOUT_S,
        )
        try:
            await r.delete(active_run_key(slug))
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run clear failed "
            f"({type(e).__name__}: {e})"
        )


async def _record_terminal_metrics(
    graph,
    config: dict,
    *,
    fallback_slug: str,
    fallback_mode: str,
    status: str,
    duration_s: float | None,
) -> None:
    try:
        snap = await graph.aget_state(config)
        state = dict(snap.values or {})
        framework = str(state.get("framework_slug") or fallback_slug or "unknown")
        mode = str(state.get("planner_mode") or fallback_mode or "unknown")
        plan_write_stats = state.get("plan_write_stats") or {}
        select_stats = state.get("select_stats") or {}
        chapter_count = (
            plan_write_stats.get("n_chapters")
            or select_stats.get("n_chapters_out")
        )
        record_planner_run(
            framework = framework,
            mode = mode,
            outcome = status,
            duration_s = duration_s,
            chapter_count = (
                int(chapter_count)
                if chapter_count is not None else
                None
            ),
        )
    except Exception as e:
        logger.warning(
            f"[planner] terminal metric emit failed: {type(e).__name__}: {e}"
        )


async def _build_langfuse_output(
    graph,
    config: dict,
    *,
    fallback_slug: str,
    fallback_mode: str,
    status: str,
    error: str | None,
) -> dict:
    output = {
        "status": status,
        "framework_slug": fallback_slug or "unknown",
        "mode": fallback_mode or "unknown",
    }
    if error:
        output["error"] = error
    try:
        snap = await graph.aget_state(config)
        state = dict(snap.values or {})
        output["framework_slug"] = str(
            state.get("framework_slug") or fallback_slug or "unknown",
        )
        output["mode"] = str(
            state.get("planner_mode") or fallback_mode or "unknown",
        )
        select_stats = state.get("select_stats") or {}
        plan_write_stats = state.get("plan_write_stats") or {}
        order_stats = state.get("order_chapters_stats") or {}
        chapter_titles = (
            plan_write_stats.get("chapter_titles")
            or select_stats.get("chapter_titles")
            or order_stats.get("chapter_titles")
            or []
        )
        output["chapter_count"] = (
            plan_write_stats.get("n_chapters")
            or select_stats.get("n_chapters_out")
            or order_stats.get("n_chapters")
            or 0
        )
        if chapter_titles:
            output["chapter_titles"] = list(chapter_titles)[:12]
    except Exception as e:
        logger.warning(
            f"[planner] langfuse output summary failed: {type(e).__name__}: {e}"
        )
    return output


async def _await_with_watcher(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
    t0: Optional[float] = None,
    slug: Optional[str] = None,
    mode: str = "llm",
) -> dict:
    """Common terminal-status lifecycle: await the planner task, write
    terminal status to checkpoint, emit SSE terminal event, cancel watcher.

    `t0` (monotonic start) → wall-clock carried IN the terminal event
    (post-terminal events would be missed) + persisted for the cached
    navbar path."""
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

    try:
        from domains.dd.runtime.llm_counter import snapshot as _snapshot_llm
        await _snapshot_llm(thread_id)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: llm-counter snapshot failed "
            f"({type(e).__name__}: {e})"
        )

    total_wall_ms = (
        int((time.monotonic() - t0) * 1000) if t0 is not None else None
    )
    await _record_terminal_metrics(
        graph,
        config,
        fallback_slug = slug or "",
        fallback_mode = mode,
        status = terminal_patch.get("status", "unknown"),
        duration_s = (
            max(total_wall_ms / 1000.0, 0.0)
            if total_wall_ms is not None else
            None
        ),
    )
    if total_wall_ms is not None and slug:
        await _persist_planner_timing(slug, total_wall_ms)

    # Clear the live-run registry; derive slug from thread_id when caller
    # didn't pass it (resume path). Format: `docs-distiller/{slug}/{uuid}`.
    reg_slug = slug or (
        thread_id.split("/")[1] if thread_id.count("/") >= 2 else None
    )
    if reg_slug:
        await _clear_active_run(reg_slug)

    await emit_progress(
        thread_id, "planner", "terminal",
        status = terminal_patch.get("status", "unknown"),
        error = terminal_patch.get("error"),
        total_wall_ms = total_wall_ms,
    )

    langfuse_output = await _build_langfuse_output(
        graph,
        config,
        fallback_slug = slug or "",
        fallback_mode = mode,
        status = terminal_patch.get("status", "unknown"),
        error = terminal_patch.get("error"),
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
        "langfuse_output": langfuse_output,
    }


async def run_planner_async(
    thread_id: str,
    slug: str,
    mode: str = "llm",
) -> dict:
    """Fresh planner kickoff. Builds graph + initial state, spawns the
    LangGraph task + cancel watcher, awaits terminal."""
    from infra.langfuse.sessions import session as _lf_session
    with _lf_session(
        "dd-planner",
        session_id = thread_id,
        user_id    = slug,
        study_id   = thread_id,
        framework  = slug,
    ):
        with get_tracer().start_as_current_span(
            "dd.planner.run",
            attributes = {
                "dd.domain":             "planner",
                "dd.run.kind":           "planner",
                "planner.thread_id":     thread_id,
                "planner.framework_slug": slug,
                "planner.mode":          mode,
            },
        ):
            set_current_span_langfuse_io(input_data = {
                "framework_slug": slug,
                "mode": mode,
                "thread_id": thread_id,
            })
            set_current_span_langfuse_trace_metadata({
                "pipeline": "dd_planner",
                "run_kind": "planner",
                "framework_slug": slug,
                "mode": mode,
                "thread_id": thread_id,
            })
            set_current_span_langfuse_observation_metadata({
                "framework_slug": slug,
                "mode": mode,
            })
            result = await _run_planner_async_inner(thread_id, slug, mode)
            set_current_span_langfuse_io(
                output_data = result.get("langfuse_output") or {
                    "status": result.get("status", "unknown"),
                    "error": result.get("error"),
                    "framework_slug": slug,
                    "mode": mode,
                },
            )
            return result


async def _run_planner_async_inner(
    thread_id: str,
    slug: str,
    mode: str = "llm",
) -> dict:
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
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

    # Total wall-clock (span, not sum — nodes can fan out in parallel).
    t0 = time.monotonic()
    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
        t0 = t0, slug = slug, mode = mode,
    )


async def run_missing_nodes_async(
    thread_id: str,
    missing: list[str],
) -> dict:
    """Catch-up worker — invokes each missing IMPLEMENTED node via
    NODE_REGISTRY directly and patches state. Needed when a thread reached
    END BEFORE a new IMPLEMENTED node was added — `ainvoke(None)` would
    short-circuit because the old checkpoint's END is already consumed."""
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
        terminal_patch = {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }
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

    try:
        from domains.dd.runtime.llm_counter import snapshot as _snapshot_llm
        await _snapshot_llm(thread_id)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: catch-up llm-counter snapshot failed "
            f"({type(e).__name__}: {e})"
        )

    await emit_progress(
        thread_id, "planner", "terminal",
        status = terminal_patch.get("status", "unknown"),
        error = terminal_patch.get("error"),
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
    }


async def resume_planner_async(thread_id: str) -> dict:
    """Resume from last checkpoint. Three sub-paths handled inline."""
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
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
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
                missing = missing,
            )
            try:
                await graph.aupdate_state(config, {"status": "running"})
            except Exception as e:
                logger.warning(
                    f"[planner] {thread_id}: pre-catch-up status reset "
                    f"failed: {type(e).__name__}: {e}"
                )
            return await run_missing_nodes_async(thread_id, missing)
        await emit_progress(
            thread_id, "planner", "terminal",
            status = "done", error = None,
        )
        return {
            "thread_id": thread_id,
            "status": "done",
            "error": None,
        }

    # Standard LangGraph resume.
    await emit_progress(
        thread_id, "planner", "resumed",
        next_nodes = list(snap.next or []),
    )
    main_task = asyncio.create_task(graph.ainvoke(None, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
    )
