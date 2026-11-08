"""Running the planner graph: a fresh run, a resume, and how a run ends (checkpoint, metrics, timing, Langfuse output).

The worker's composition root (`domains/dd/pipeline/ai`) binds the graph, object storage and run context to COELHO Cloud and hands them in as a `Binding`."""
from __future__ import annotations
import domains
from . import params

import time


async def run(binding: domains.dd.pipeline.runtime.runner.entities.Binding, thread_id: str, slug: str, mode: str = "llm") -> dict:
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


async def _run(binding: domains.dd.pipeline.runtime.runner.entities.Binding, thread_id: str, slug: str, mode: str) -> dict:
    config = binding.config(thread_id)
    await domains.dd.pipeline.runtime.cancel.service.reset(thread_id)
    initial_state = {"framework_slug": slug, "thread_id": thread_id, "planner_mode": mode, "status": "running"}

    # Total wall-clock (span, not sum: stages fan out in parallel).
    t0 = time.monotonic()
    main_task, watcher_task = domains.dd.pipeline.runtime.runner.service.launch(binding, initial_state, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id, t0 = t0, slug = slug, mode = mode)


async def resume(binding: domains.dd.pipeline.runtime.runner.entities.Binding, thread_id: str) -> dict:
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
    terminal_status = dict(snap.values or {}).get("status")
    if terminal_status in {"done", "needs_review"}:
        terminal_error = dict(snap.values or {}).get("error")
        await progress.emit_progress(thread_id, params.STAGE, "terminal", status = terminal_status, error = terminal_error)
        return {"thread_id": thread_id, "status": terminal_status, "error": terminal_error}

    await progress.emit_progress(thread_id, params.STAGE, "resumed", next_nodes = list(snap.next or []))
    main_task, watcher_task = domains.dd.pipeline.runtime.runner.service.launch(binding, None, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id)


async def _finish(
    binding: domains.dd.pipeline.runtime.runner.entities.Binding,
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
    patch = await domains.dd.pipeline.runtime.runner.service.settle(binding, config, main_task, watcher_task, thread_id, params.STAGE)
    await domains.dd.pipeline.runtime.runner.service.snapshot_usage(thread_id, params.STAGE)

    total_wall_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else None
    status = patch.get("status", "unknown")
    await domains.dd.pipeline.observability.service.record_planner_terminal(
        binding.graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status,
        duration_s = max(total_wall_ms / 1000.0, 0.0) if total_wall_ms is not None else None,
    )
    if total_wall_ms is not None and slug:
        await domains.dd.pipeline.storage.service.write_planner_timing(binding.blobs, slug, total_wall_ms)

    # The slug comes from the thread id on the resume path (format: docs-distiller/{slug}/{uuid}).
    registered = slug or domains.dd.pipeline.runtime.dispatch.domain.slug_from_thread_id(thread_id)
    if registered:
        await domains.dd.pipeline.runtime.dispatch.service.clear_active_run(registered)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        thread_id, params.STAGE, "terminal", status = status, error = patch.get("error"), total_wall_ms = total_wall_ms,
    )
    langfuse_output = await domains.dd.pipeline.observability.service.planner_langfuse_output(
        binding.graph, config, fallback_slug = slug or "", fallback_mode = mode, status = status, error = patch.get("error"),
    )
    return {"thread_id": thread_id, "status": status, "error": patch.get("error"), "langfuse_output": langfuse_output}
