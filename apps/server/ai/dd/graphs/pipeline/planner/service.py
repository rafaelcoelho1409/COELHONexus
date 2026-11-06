"""Running the planner graph: a fresh run, a resume, and how a run ends (checkpoint, metrics, timing, Langfuse output).

The worker's composition root (`domains/dd/pipeline/ai`) binds the graph, object storage and run context to COELHO Cloud and hands them in as a `Binding`."""
from __future__ import annotations
import domains
from .. import runner

import json
import logging
import time


logger = logging.getLogger(__name__)

STAGE = "planner"


async def run(binding: runner.entities.Binding, thread_id: str, slug: str, mode: str = "llm") -> dict:
    """Fresh planner kickoff: the initial state, the graph task and the cancel watcher, then the terminal state."""
    with domains.dd.pipeline.observability.service.workflow_span(
        "dd.planner.run",
        workflow = "dd_planner",
        session_name = "dd-planner",
        session_fields = {"session_id": thread_id, "user_id": slug, "study_id": thread_id, "framework": slug},
        attributes = {
            "dd.domain": "planner",
            "dd.run.kind": "planner",
            "planner.thread_id": thread_id,
            "planner.framework_slug": slug,
            "planner.mode": mode,
        },
        input_data = {"framework_slug": slug, "mode": mode, "thread_id": thread_id},
        trace_metadata = {"pipeline": "dd_planner", "run_kind": "planner", "framework_slug": slug, "mode": mode, "thread_id": thread_id},
        observation_metadata = {"framework_slug": slug, "mode": mode},
    ) as set_output:
        result = await _run(binding, thread_id, slug, mode)
        set_output(result.get("langfuse_output") or {
            "status": result.get("status", "unknown"), "error": result.get("error"), "framework_slug": slug, "mode": mode,
        })
        return result


async def _run(binding: runner.entities.Binding, thread_id: str, slug: str, mode: str) -> dict:
    config = binding.config(thread_id)
    await domains.dd.pipeline.runtime.cancel.service.reset(thread_id)
    initial_state = {"framework_slug": slug, "thread_id": thread_id, "planner_mode": mode, "status": "running"}

    # Total wall-clock (span, not sum: stages fan out in parallel).
    t0 = time.monotonic()
    main_task, watcher_task = runner.service.launch(binding, initial_state, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id, t0 = t0, slug = slug, mode = mode)


async def resume(binding: runner.entities.Binding, thread_id: str) -> dict:
    """Resume from the last checkpoint: a thread that already finished only re-announces it; any other continues at the node it stopped in."""
    config = binding.config(thread_id)
    snap = await binding.graph.aget_state(config)
    if snap.values == {}:
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": f"no checkpoints found for thread_id={thread_id!r}; call POST /pipeline/planner/{{slug}} to start a fresh run",
        }

    progress = domains.dd.pipeline.runtime.progress.service
    await domains.dd.pipeline.runtime.cancel.service.reset(thread_id)
    if dict(snap.values or {}).get("status") == "done":
        await progress.emit_progress(thread_id, STAGE, "terminal", status = "done", error = None)
        return {"thread_id": thread_id, "status": "done", "error": None}

    await progress.emit_progress(thread_id, STAGE, "resumed", next_nodes = list(snap.next or []))
    main_task, watcher_task = runner.service.launch(binding, None, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id)


async def _finish(
    binding: runner.entities.Binding,
    config: dict,
    main_task,
    watcher_task,
    thread_id: str,
    t0: float | None = None,
    slug: str | None = None,
    mode: str = "llm",
) -> dict:
    """Settle the run, then report it: usage snapshot, metrics, timing, live-run registry, terminal event. The wall-clock rides in the terminal event so it
    survives a subscriber that disconnected."""
    patch = await runner.service.settle(binding, config, main_task, watcher_task, thread_id, STAGE)
    await runner.service.snapshot_usage(thread_id, STAGE)

    total_wall_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else None
    status = patch.get("status", "unknown")
    await _record_terminal_metrics(
        binding.graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status,
        duration_s = max(total_wall_ms / 1000.0, 0.0) if total_wall_ms is not None else None,
    )
    if total_wall_ms is not None and slug:
        await _persist_timing(binding.blobs, slug, total_wall_ms)

    # The slug comes from the thread id on the resume path (format: docs-distiller/{slug}/{uuid}).
    registered = slug or domains.dd.pipeline.runtime.dispatch.domain.slug_from_thread_id(thread_id)
    if registered:
        await domains.dd.pipeline.runtime.dispatch.service.clear_active_run(registered)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        thread_id, STAGE, "terminal", status = status, error = patch.get("error"), total_wall_ms = total_wall_ms,
    )
    langfuse_output = await _langfuse_output(
        binding.graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status, error = patch.get("error"),
    )
    return {"thread_id": thread_id, "status": status, "error": patch.get("error"), "langfuse_output": langfuse_output}


async def _persist_timing(blobs, slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        await blobs.write(
            domains.dd.pipeline.storage.keys.planner_timing_key(slug),
            json.dumps({"slug": slug, "total_wall_ms": int(total_wall_ms), "finished_ts": time.time()}, indent = 2),
            content_type = "application/json",
        )
    except Exception as error:
        logger.warning(f"[planner] {slug}: timing persist failed ({type(error).__name__}: {error})")


async def _record_terminal_metrics(graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, duration_s: float | None) -> None:
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


async def _langfuse_output(graph, config: dict, *, fallback_slug: str, fallback_mode: str, status: str, error: str | None) -> dict:
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
