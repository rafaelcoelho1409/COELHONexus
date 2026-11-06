"""The planner's composition root: builds the graph's run context from COELHO Cloud services, runs or resumes the graph in the
worker, forwards its progress events to the run's channel, and reports how the run ended (checkpoint, metrics, Langfuse, timing)."""
from __future__ import annotations
import ai, domains, infra

import asyncio
import json
import logging
import time
from typing import Any


logger = logging.getLogger(__name__)


def planner_graph():
    """The planner graph over the shared checkpointer. Reading a thread's state needs nothing else: the run's dependencies are the `context` of a run."""
    return ai.dd.graphs.pipeline.planner.graph.build_planner_graph(checkpointer = infra.postgres.service.get_checkpointer())


def node_order() -> tuple[str, ...]:
    """The planner's stages in the order they run (what `/info` reports and the web's stage board is checked against)."""
    return ai.dd.graphs.pipeline.planner.graph.NODE_ORDER


def build_context() -> Any:
    """A run's context: object storage and its agents (built on first use, from the endpoint the Settings page currently holds)."""
    domains.settings.chat.service.refresh_endpoint(force = True)
    return ai.dd.graphs.pipeline.planner.context.PlannerContext(blobs = infra.minio.service.get_storage())


async def init_and_run(coro):
    """Ensure the AsyncPostgresSaver is open in this worker's interpreter (idempotent module-scope cache), then run `coro`."""
    await infra.postgres.service.init_checkpointer()
    return await coro


def release_planner_lock(slug: str, thread_id: str) -> None:
    """Best-effort sync CAD release from the task's finally block. Failure → stuck lock until TTL expiry; never raised."""
    if not slug or not thread_id:
        return
    try:
        with infra.redis.service.client_sync() as r:
            infra.redis.service.release_if_owner_sync(r, domains.dd.pipeline.runtime.keys.lock_key(slug), thread_id)
    except Exception as error:
        logger.warning(f"[task] planner lock release failed for slug={slug!r}: {type(error).__name__}: {error}")


async def _persist_planner_timing(slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        await infra.minio.service.get_storage().write(
            domains.dd.pipeline.storage.keys.planner_timing_key(slug),
            json.dumps({"slug": slug, "total_wall_ms": int(total_wall_ms), "finished_ts": time.time()}, indent = 2),
            content_type = "application/json",
        )
    except Exception as error:
        logger.warning(f"[planner] {slug}: timing persist failed ({type(error).__name__}: {error})")


async def _record_terminal_metrics(
    graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, duration_s: float | None,
) -> None:
    try:
        values = dict((await graph.aget_state(config)).values or {})
        plan_write_stats = values.get("plan_write_stats") or {}
        select_stats = values.get("select_stats") or {}
        chapter_count = plan_write_stats.get("n_chapters") or select_stats.get("n_chapters_out")
        domains.dd.pipeline.observability.service.record_planner_run(
            framework = str(values.get("framework_slug") or fallback_slug or "unknown"),
            mode = str(values.get("planner_mode") or fallback_mode or "unknown"),
            outcome = status,
            duration_s = duration_s,
            chapter_count = int(chapter_count) if chapter_count is not None else None,
        )
    except Exception as error:
        logger.warning(f"[planner] terminal metric emit failed: {type(error).__name__}: {error}")


async def _langfuse_output(
    graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, error: str | None,
) -> dict:
    output = {"status": status, "framework_slug": fallback_slug or "unknown", "mode": fallback_mode or "unknown"}
    if error:
        output["error"] = error
    try:
        values = dict((await graph.aget_state(config)).values or {})
        output = domains.dd.pipeline.runtime.dispatch.domain.derive_langfuse_output(
            values, fallback_slug = fallback_slug, fallback_mode = fallback_mode, status = status, error = error,
        )
    except Exception as exc:
        logger.warning(f"[planner] langfuse output summary failed: {type(exc).__name__}: {exc}")
    return output


async def _drive(graph, graph_input: dict | None, config: dict, context: Any, thread_id: str) -> None:
    """Run the graph and republish its progress events on the run's channel, in the shape the web reads.

    Nodes emit `{step, kind, ...}` through the graph's `custom` stream (also from the fan-out workers of a stage's subgraph, hence
    `subgraphs=True`); `durability="sync"` writes each checkpoint before the next step starts, so a worker lost mid-run resumes
    from its last completed node."""
    async for _namespace, _mode, chunk in graph.astream(
        graph_input, config, context = context, stream_mode = ["custom"], subgraphs = True, durability = "sync",
    ):
        if not isinstance(chunk, dict) or "step" not in chunk or "kind" not in chunk:
            continue
        fields = {k: v for k, v in chunk.items() if k not in {"step", "kind"}}
        await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, chunk["step"], chunk["kind"], **fields)


async def _await_with_watcher(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
    t0: float | None = None,
    slug: str | None = None,
    mode: str = "llm",
) -> dict:
    """Await the planner task, write the terminal status and event; the wall-clock rides in the terminal event so it survives a subscriber that disconnected."""
    terminal_patch: dict = {}
    try:
        await main_task
        terminal_patch["status"] = "done"
        logger.info(f"[planner] {thread_id}: done")
    except asyncio.CancelledError:
        terminal_patch["status"] = "cancelled"
        logger.info(f"[planner] {thread_id}: cancelled by user")
    except Exception as error:
        terminal_patch["status"] = "failed"
        terminal_patch["error"] = f"{type(error).__name__}: {error}"
        logger.exception(f"[planner] {thread_id}: run failed ({type(error).__name__}: {error})")
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except (asyncio.CancelledError, Exception):
            pass

    try:
        await graph.aupdate_state(config, terminal_patch)
    except Exception as error:
        logger.warning(f"[planner] {thread_id}: aupdate_state failed for terminal patch {terminal_patch!r}: {type(error).__name__}: {error}")
    try:
        await domains.settings.llm_counter.service.snapshot("dd", thread_id)
    except Exception as error:
        logger.warning(f"[planner] {thread_id}: llm-counter snapshot failed ({type(error).__name__}: {error})")

    total_wall_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else None
    status = terminal_patch.get("status", "unknown")
    await _record_terminal_metrics(
        graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status,
        duration_s = max(total_wall_ms / 1000.0, 0.0) if total_wall_ms is not None else None,
    )
    if total_wall_ms is not None and slug:
        await _persist_planner_timing(slug, total_wall_ms)

    # The slug comes from the thread id on the resume path (format: docs-distiller/{slug}/{uuid}).
    registered = slug or domains.dd.pipeline.runtime.dispatch.domain.slug_from_thread_id(thread_id)
    if registered:
        await domains.dd.pipeline.runtime.dispatch.service.clear_active_run(registered)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        thread_id, "planner", "terminal", status = status, error = terminal_patch.get("error"), total_wall_ms = total_wall_ms,
    )
    langfuse_output = await _langfuse_output(
        graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status, error = terminal_patch.get("error"),
    )
    return {
        "thread_id": thread_id,
        "status": status,
        "error": terminal_patch.get("error"),
        "langfuse_output": langfuse_output,
    }


def _config(thread_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "callbacks": [c for c in (infra.langfuse.service.build_langchain_callback(),) if c is not None],
    }


async def run_planner_async(thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner kickoff: builds the graph and the initial state, spawns the graph task and the cancel watcher, awaits the terminal state."""
    with infra.langfuse.service.session(
        "dd-planner", session_id = thread_id, user_id = slug, study_id = thread_id, framework = slug,
    ):
        with infra.opentelemetry.service.get_tracer().start_as_current_span(
            "dd.planner.run",
            attributes = {
                infra.langfuse.keys.KEEP: True,
                infra.langfuse.keys.KIND: "workflow_root",
                infra.langfuse.keys.TRACE_NAME: "dd.planner.run",
                "dd.domain": "planner",
                "dd.run.kind": "planner",
                "planner.thread_id": thread_id,
                "planner.framework_slug": slug,
                "planner.mode": mode,
                infra.langfuse.keys.OBS_META_WORKFLOW: "dd_planner",
            },
        ):
            infra.langfuse.service.set_current_span_langfuse_io(
                input_data = {"framework_slug": slug, "mode": mode, "thread_id": thread_id},
            )
            infra.langfuse.service.set_current_span_langfuse_trace_metadata({
                "pipeline": "dd_planner", "run_kind": "planner", "framework_slug": slug, "mode": mode, "thread_id": thread_id,
            })
            infra.langfuse.service.set_current_span_langfuse_observation_metadata({"framework_slug": slug, "mode": mode})
            result = await _run_planner_async_inner(thread_id, slug, mode)
            infra.langfuse.service.set_current_span_langfuse_io(
                output_data = result.get("langfuse_output") or {
                    "status": result.get("status", "unknown"), "error": result.get("error"), "framework_slug": slug, "mode": mode,
                },
            )
            return result


async def _run_planner_async_inner(thread_id: str, slug: str, mode: str = "llm") -> dict:
    graph = planner_graph()
    config = _config(thread_id)
    async with infra.redis.service.client() as r:
        await domains.dd.pipeline.runtime.cancel.service.clear_cancel(r, thread_id)
    initial_state = {"framework_slug": slug, "thread_id": thread_id, "planner_mode": mode, "status": "running"}

    # Total wall-clock (span, not sum: stages fan out in parallel).
    t0 = time.monotonic()
    main_task = asyncio.create_task(_drive(graph, initial_state, config, build_context(), thread_id))
    watcher_task = asyncio.create_task(domains.dd.pipeline.runtime.cancel.service.watcher(thread_id, main_task))
    return await _await_with_watcher(graph, config, main_task, watcher_task, thread_id, t0 = t0, slug = slug, mode = mode)


async def resume_planner_async(thread_id: str) -> dict:
    """Resume from the last checkpoint: a thread that already finished only re-announces it; any other continues at the node it stopped in."""
    graph = planner_graph()
    config = _config(thread_id)
    snap = await graph.aget_state(config)
    if snap.values == {}:
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": f"no checkpoints found for thread_id={thread_id!r}; call POST /pipeline/planner/{{slug}} to start a fresh run",
        }

    async with infra.redis.service.client() as r:
        await domains.dd.pipeline.runtime.cancel.service.clear_cancel(r, thread_id)
    if dict(snap.values or {}).get("status") == "done":
        await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, "planner", "terminal", status = "done", error = None)
        return {"thread_id": thread_id, "status": "done", "error": None}

    await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, "planner", "resumed", next_nodes = list(snap.next or []))
    main_task = asyncio.create_task(_drive(graph, None, config, build_context(), thread_id))
    watcher_task = asyncio.create_task(domains.dd.pipeline.runtime.cancel.service.watcher(thread_id, main_task))
    return await _await_with_watcher(graph, config, main_task, watcher_task, thread_id)
