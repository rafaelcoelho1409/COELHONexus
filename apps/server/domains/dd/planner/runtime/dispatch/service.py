"""Async planner orchestration (kickoff, resume, catch-up) shared by FastAPI and Celery — same logic, different runtime location."""
from __future__ import annotations
import domains
import infra
from . import domain

import asyncio
import json
import logging
import time
from typing import Optional


logger = logging.getLogger(__name__)


async def _persist_planner_timing(slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        await infra.minio.service.get_storage().write(
            domains.dd.planner.keys.planner_timing_key(slug),
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
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.planner.keys.active_run_key(slug))
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run clear failed "
            f"({type(e).__name__}: {e})"
        )


# ---------------------------------------------------------------------------
# Run gate + registry — what the API does around enqueueing a run: single-flight
# lock, cross-stage block, live-run registry, cancel, wipe.
# ---------------------------------------------------------------------------

async def reserve_start(slug: str, thread_id: str) -> dict | None:
    """Single-flight gate for starting a planner run on `slug`. Returns the `locked` response when a synth is running
    (planner and synth share the LLM resources), another slug's planner is, or this slug's planner lock is held; else
    None, with the slug's lock taken for `thread_id` and its cancel flag cleared — the caller then enqueues the run
    and calls `release_start` if that fails."""
    redis = infra.redis.service
    async with redis.client() as r:
        synth = await redis.first_held(r, domains.dd.synth.keys.lock_prefix())
        if synth is not None:
            synth_slug, synth_thread = synth
            return {
                "status": "locked",
                "slug": synth_slug,
                "thread_id": synth_thread,
                "stage": "synth",
                "message": (
                    f"A synth is running ({synth_slug!r}, "
                    f"thread_id={synth_thread}). Planner and Synth "
                    f"share the same LLM resources — running both "
                    f"at once degrades quality on each. Wait for "
                    f"the synth to finish or cancel it before "
                    f"starting a planner."
                ),
            }
        other = await redis.first_held(r, domains.dd.planner.keys.lock_prefix(), skip=slug)
        if other is not None:
            other_slug, other_thread = other
            return {
                "status": "locked",
                "slug": other_slug,
                "thread_id": other_thread,
                "stage": "planner",
                "message": (
                    f"Another planner is running ({other_slug!r}, "
                    f"thread_id={other_thread}). Wait for it to "
                    f"finish or cancel it before starting {slug!r}."
                ),
            }
        acquired, existing = await redis.try_lock(
            r, domains.dd.planner.keys.lock_key(slug), thread_id, domains.dd.planner.params.LOCK_TTL_S,
        )
        if not acquired:
            return {
                "status": "locked",
                "slug": slug,
                "thread_id": existing,
                "stage": "planner",
                "message": (
                    f"A planner of {slug!r} is already running "
                    f"(thread_id={existing}). Wait for it to finish "
                    f"or cancel it before retrying."
                ),
            }
        await domains.dd.planner.runtime.cancel.service.clear_cancel(r, thread_id)
    return None


async def release_start(slug: str) -> None:
    """Drop the slug's planner lock after a failed enqueue (best-effort: never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.delete(domains.dd.planner.keys.lock_key(slug))
    except Exception:
        pass


async def register_active_run(slug: str, thread_id: str) -> None:
    """Record the live run so a page refresh can reconnect to it (best-effort: logged, never raised)."""
    try:
        async with infra.redis.service.client() as r:
            await r.set(
                domains.dd.planner.keys.active_run_key(slug),
                json.dumps({"thread_id": thread_id, "started_ts": time.time()}),
                ex = domains.dd.planner.params.ACTIVE_RUN_TTL_S,
            )
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run register failed: "
            f"{type(e).__name__}: {e}"
        )


async def read_active_run(slug: str) -> dict:
    """Page-refresh recovery: `{"active": True, "thread_id", "started_ts"}` for the slug's live run, else
    `{"active": False}` (also when Redis is unreachable)."""
    try:
        async with infra.redis.service.client() as r:
            raw = await r.get(domains.dd.planner.keys.active_run_key(slug))
    except Exception:
        return {"active": False}
    if not raw:
        return {"active": False}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
        return {
            "active": True,
            "thread_id": data.get("thread_id"),
            "started_ts": data.get("started_ts"),
        }
    except Exception:
        return {"active": True, "thread_id": str(raw), "started_ts": None}


async def active_lock() -> dict | None:
    """The planner run holding a lock right now — `{"slug", "thread_id"}` — or None."""
    async with infra.redis.service.client() as r:
        held = await infra.redis.service.first_held(r, domains.dd.planner.keys.lock_prefix())
    return None if held is None else {"slug": held[0], "thread_id": held[1]}


async def cancel_run(thread_id: str) -> None:
    """Raise the thread's cancel flag and drop its slug's live-run registry entry."""
    async with infra.redis.service.client() as r:
        await domains.dd.planner.runtime.cancel.service.request_cancel(r, thread_id)
        if thread_id.count("/") >= 2:
            await r.delete(domains.dd.planner.keys.active_run_key(thread_id.split("/")[1]))


async def wipe_redis(slug: str) -> int:
    """Delete the slug's live-run registry and lock (so the next start is not rejected by a stale lock); returns how
    many keys went (0 when Redis fails — logged)."""
    try:
        async with infra.redis.service.client() as r:
            return await r.delete(
                domains.dd.planner.keys.active_run_key(slug),
                domains.dd.planner.keys.lock_key(slug),
            )
    except Exception as e:
        logger.warning(f"[planner-wipe] Redis delete failed for {slug!r}: {e}")
        return 0


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
        domains.dd.planner.observability.service.record_planner_run(
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
        output = domain.derive_langfuse_output(
            state,
            fallback_slug = fallback_slug, fallback_mode = fallback_mode,
            status = status, error = error,
        )
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
    """Await the planner task, write terminal status + SSE event; wall-clock carried inside the terminal event so it's not lost if the subscriber disconnects after."""
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
        await domains.settings.llm_counter.service.snapshot("dd", thread_id)
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

    # Derive slug from thread_id on resume path (format: docs-distiller/{slug}/{uuid}).
    reg_slug = slug or (
        thread_id.split("/")[1] if thread_id.count("/") >= 2 else None
    )
    if reg_slug:
        await _clear_active_run(reg_slug)

    await domains.dd.planner.runtime.progress.service.emit_progress(
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
    with infra.langfuse.service.session(
        "dd-planner",
        session_id = thread_id,
        user_id    = slug,
        study_id   = thread_id,
        framework  = slug,
    ):
        with infra.opentelemetry.service.get_tracer().start_as_current_span(
            "dd.planner.run",
            attributes = {
                infra.langfuse.keys.KEEP:  True,
                infra.langfuse.keys.KIND:  "workflow_root",
                infra.langfuse.keys.TRACE_NAME:    "dd.planner.run",
                "dd.domain":             "planner",
                "dd.run.kind":           "planner",
                "planner.thread_id":     thread_id,
                "planner.framework_slug": slug,
                "planner.mode":          mode,
                infra.langfuse.keys.OBS_META_WORKFLOW: "dd_planner",
            },
        ):
            infra.langfuse.service.set_current_span_langfuse_io(input_data = {
                "framework_slug": slug,
                "mode": mode,
                "thread_id": thread_id,
            })
            infra.langfuse.service.set_current_span_langfuse_trace_metadata({
                "pipeline": "dd_planner",
                "run_kind": "planner",
                "framework_slug": slug,
                "mode": mode,
                "thread_id": thread_id,
            })
            infra.langfuse.service.set_current_span_langfuse_observation_metadata({
                "framework_slug": slug,
                "mode": mode,
            })
            result = await _run_planner_async_inner(thread_id, slug, mode)
            infra.langfuse.service.set_current_span_langfuse_io(
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
    graph = domains.dd.planner.graph.build_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [c for c in (infra.langfuse.service.build_langchain_callback(),) if c is not None],
    }

    async with infra.redis.service.client() as r:
        await domains.dd.planner.runtime.cancel.service.clear_cancel(r, thread_id)

    initial_state = {
        "framework_slug": slug,
        "thread_id": thread_id,
        "planner_mode": mode,
        "status": "running",
    }

    # Total wall-clock (span, not sum — nodes can fan out in parallel).
    t0 = time.monotonic()
    # durability="sync" — checkpoint written BEFORE the next super-step,
    # not the library default "async" (checkpoint persisted during the
    # next step, small crash-window risk). DD's whole resume/catch-up
    # story depends on every completed node's checkpoint actually
    # landing (confirmed incident category: a redeploy mid-run kills the
    # worker — see [[project_synth_redeploy_interrupts]] memory), and
    # nodes here are LLM/MinIO-call-dominated (seconds to minutes), so a
    # synchronous single-row Postgres INSERT is immaterial overhead.
    main_task = asyncio.create_task(
        graph.ainvoke(initial_state, config, durability = "sync"),
    )
    watcher_task = asyncio.create_task(domains.dd.planner.runtime.cancel.service.watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
        t0 = t0, slug = slug, mode = mode,
    )


async def run_missing_nodes_async(
    thread_id: str,
    missing: list[str],
) -> dict:
    """Catch-up worker for threads that reached END before a new IMPLEMENTED node was added (ainvoke(None) would short-circuit at the consumed END marker)."""
    graph = domains.dd.planner.graph.build_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [c for c in (infra.langfuse.service.build_langchain_callback(),) if c is not None],
    }

    terminal_patch: dict = {"status": "done"}
    try:
        for name in missing:
            node_fn = domains.dd.planner.graph.NODE_REGISTRY.get(name)
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
        await domains.settings.llm_counter.service.snapshot("dd", thread_id)
    except Exception as e:
        logger.warning(
            f"[planner] {thread_id}: catch-up llm-counter snapshot failed "
            f"({type(e).__name__}: {e})"
        )

    await domains.dd.planner.runtime.progress.service.emit_progress(
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
    graph = domains.dd.planner.graph.build_graph()
    config = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [c for c in (infra.langfuse.service.build_langchain_callback(),) if c is not None],
    }

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

    async with infra.redis.service.client() as r:
        await domains.dd.planner.runtime.cancel.service.clear_cancel(r, thread_id)

    state = dict(snap.values or {})
    if state.get("status") == "done":
        missing = domain.missing_implemented_nodes(state)
        if missing:
            await domains.dd.planner.runtime.progress.service.emit_progress(
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
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "planner", "terminal",
            status = "done", error = None,
        )
        return {
            "thread_id": thread_id,
            "status": "done",
            "error": None,
        }

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "planner", "resumed",
        next_nodes = list(snap.next or []),
    )
    # durability="sync" — see rationale in run_planner_async above.
    main_task = asyncio.create_task(
        graph.ainvoke(None, config, durability = "sync"),
    )
    watcher_task = asyncio.create_task(domains.dd.planner.runtime.cancel.service.watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
    )
