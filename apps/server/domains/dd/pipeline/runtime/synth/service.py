"""Running one chapter of the synth: a fresh run, a resume, and how a run ends (checkpoint, metrics, usage, timing).

The worker's composition root (`domains/dd/pipeline/ai/synth`) binds the graph, object storage and run context to COELHO Cloud and hands them in as a `Binding`."""
from __future__ import annotations
import domains
from . import params

import asyncio
import logging
import time


logger = logging.getLogger(__name__)


async def ensure_corpus_current(slug: str) -> None:
    """Re-normalize the slug's stored pages if the normalizer has moved on since ingestion (no-op otherwise). Never blocks a synth run."""
    try:
        await domains.dd.ingestion.backfill.service.ensure_framework_normalized(slug)
    except Exception as error:
        logger.warning(f"[synth] {slug}: ensure_framework_normalized failed ({type(error).__name__}: {error}) — continuing on stored pages")


async def run_chapter(binding: domains.dd.pipeline.runtime.runner.entities.Binding, thread_id: str, slug: str, chapter_id: str, mode: str = "quality") -> dict:
    """Fresh per-chapter run: the initial state, the graph task and the cancel watcher, then the terminal state."""
    with domains.dd.pipeline.observability.service.workflow_span(
        "dd.synth.chapter.run",
        workflow = "dd_synth",
        session_name = "dd-synth",
        session_fields = {"session_id": thread_id, "user_id": slug, "chapter_id": chapter_id, "framework": slug},
        attributes = {
            "dd.domain": "synth",
            "dd.run.kind": "chapter",
            "synth.thread_id": thread_id,
            "synth.framework_slug": slug,
            "synth.chapter_id": chapter_id,
            "synth.mode": mode,
        },
        input_data = {"framework_slug": slug, "chapter_id": chapter_id, "mode": mode, "thread_id": thread_id},
        trace_metadata = {
            "pipeline": "dd_synth", "run_kind": "chapter", "framework_slug": slug, "chapter_id": chapter_id, "mode": mode, "thread_id": thread_id,
        },
        observation_metadata = {"framework_slug": slug, "chapter_id": chapter_id, "mode": mode},
    ) as set_output:
        await ensure_corpus_current(slug)
        config = binding.config(thread_id)
        await domains.dd.pipeline.runtime.cancel.service.reset(thread_id)
        initial_state = {
            "framework_slug": slug,
            "chapter_id": chapter_id,
            "thread_id": thread_id,
            "synth_mode": mode,
            "status": "running",
            # The wall-clock RETHINK gate (`route_after_mgsr`) reads this against SINGLE_CHAPTER_SOFT_TIME_LIMIT_S.
            "run_started_at": time.time(),
        }
        chapter_t0 = time.monotonic()
        main_task, watcher_task = domains.dd.pipeline.runtime.runner.service.launch(binding, initial_state, config, thread_id)
        result = await _finish(binding, config, main_task, watcher_task, thread_id)
        if result.get("status") == "done":
            await domains.dd.pipeline.storage.service.write_chapter_timing(binding.blobs, slug, chapter_id, int((time.monotonic() - chapter_t0) * 1000))
        set_output({
            "status": result.get("status", "unknown"), "error": result.get("error"), "framework_slug": slug, "chapter_id": chapter_id, "mode": mode,
        })
        return result


async def resume_chapter(binding: domains.dd.pipeline.runtime.runner.entities.Binding, thread_id: str) -> dict:
    """Resume from the last checkpoint: a thread that already finished only re-announces it; any other continues at the node it stopped in."""
    config = binding.config(thread_id)
    snap = await binding.graph.aget_state(config)
    if snap.values == {}:
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": f"no checkpoints found for thread_id = {thread_id!r}; call POST /pipeline/synth/{{slug}} to start a fresh run",
        }
    progress = domains.dd.pipeline.runtime.progress.service
    await domains.dd.pipeline.runtime.cancel.service.reset(thread_id)
    terminal_status = dict(snap.values or {}).get("status")
    if terminal_status in {"done", "needs_review"}:
        terminal_error = dict(snap.values or {}).get("error")
        await progress.emit_progress(thread_id, params.STAGE, "terminal", status = terminal_status, error = terminal_error)
        return {"thread_id": thread_id, "status": terminal_status, "error": terminal_error}

    await progress.emit_progress(thread_id, params.STAGE, "resumed", next_nodes = list(snap.next or []))
    # A fresh wall-clock start for the RETHINK gate: this resume is its own Celery task execution with its own soft_time_limit clock.
    try:
        await binding.graph.aupdate_state(config, {"run_started_at": time.time()})
    except Exception as error:
        logger.warning(f"[synth] {thread_id}: run_started_at reset on resume failed (the wall-clock gate will use the stale value): {type(error).__name__}: {error}")
    main_task, watcher_task = domains.dd.pipeline.runtime.runner.service.launch(binding, None, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id)


async def _finish(binding: domains.dd.pipeline.runtime.runner.entities.Binding, config: dict, main_task: asyncio.Task, watcher_task: asyncio.Task, thread_id: str) -> dict:
    """Settle the chapter run, then report it: metrics, usage snapshot, terminal event."""
    patch = await domains.dd.pipeline.runtime.runner.service.settle(binding, config, main_task, watcher_task, thread_id, params.STAGE)
    status = patch.get("status", "unknown")
    await domains.dd.pipeline.observability.service.record_chapter_terminal(binding.graph, config, status)
    await domains.dd.pipeline.runtime.runner.service.snapshot_usage(thread_id, params.STAGE)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, params.STAGE, "terminal", status = status, error = patch.get("error"))
    return {"thread_id": thread_id, "status": status, "error": patch.get("error")}
