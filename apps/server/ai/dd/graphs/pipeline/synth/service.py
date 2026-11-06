"""Running one chapter of the synth: a fresh run, a resume, and how a run ends (checkpoint, metrics, usage, timing).

The worker's composition root (`domains/dd/pipeline/ai/synth`) binds the graph, object storage and run context to COELHO Cloud and hands them in as a `Binding`."""
from __future__ import annotations
import domains
from .. import runner

import asyncio
import json
import logging
import time


logger = logging.getLogger(__name__)

STAGE = "synth"


async def ensure_corpus_current(slug: str) -> None:
    """Re-normalize the slug's stored pages if the normalizer has moved on since ingestion (no-op otherwise). Never blocks a synth run."""
    try:
        await domains.dd.ingestion.backfill.service.ensure_framework_normalized(slug)
    except Exception as error:
        logger.warning(f"[synth] {slug}: ensure_framework_normalized failed ({type(error).__name__}: {error}) — continuing on stored pages")


async def run_chapter(binding: runner.entities.Binding, thread_id: str, slug: str, chapter_id: str, mode: str = "quality") -> dict:
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
            # The wall-clock RETHINK gate (router) reads this against SINGLE_CHAPTER_SOFT_TIME_LIMIT_S.
            "run_started_at": time.time(),
        }
        chapter_t0 = time.monotonic()
        main_task, watcher_task = runner.service.launch(binding, initial_state, config, thread_id)
        result = await _finish(binding, config, main_task, watcher_task, thread_id)
        if result.get("status") == "done":
            await persist_chapter_timing(binding.blobs, slug, chapter_id, int((time.monotonic() - chapter_t0) * 1000))
        set_output({
            "status": result.get("status", "unknown"), "error": result.get("error"), "framework_slug": slug, "chapter_id": chapter_id, "mode": mode,
        })
        return result


async def resume_chapter(binding: runner.entities.Binding, thread_id: str) -> dict:
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
    if dict(snap.values or {}).get("status") == "done":
        await progress.emit_progress(thread_id, STAGE, "terminal", status = "done", error = None)
        return {"thread_id": thread_id, "status": "done", "error": None}

    await progress.emit_progress(thread_id, STAGE, "resumed", next_nodes = list(snap.next or []))
    # A fresh wall-clock start for the RETHINK gate: this resume is its own Celery task execution with its own soft_time_limit clock.
    try:
        await binding.graph.aupdate_state(config, {"run_started_at": time.time()})
    except Exception as error:
        logger.warning(f"[synth] {thread_id}: run_started_at reset on resume failed (the wall-clock gate will use the stale value): {type(error).__name__}: {error}")
    main_task, watcher_task = runner.service.launch(binding, None, config, thread_id)
    return await _finish(binding, config, main_task, watcher_task, thread_id)


async def _finish(binding: runner.entities.Binding, config: dict, main_task: asyncio.Task, watcher_task: asyncio.Task, thread_id: str) -> dict:
    """Settle the chapter run, then report it: metrics, usage snapshot, terminal event."""
    patch = await runner.service.settle(binding, config, main_task, watcher_task, thread_id, STAGE)
    status = patch.get("status", "unknown")
    await record_terminal_metrics(binding.graph, config, status)
    await runner.service.snapshot_usage(thread_id, STAGE)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(thread_id, STAGE, "terminal", status = status, error = patch.get("error"))
    return {"thread_id": thread_id, "status": status, "error": patch.get("error")}


async def record_terminal_metrics(graph, config: dict, status: str) -> None:
    """Chapter outcome and audit metrics of a finished chapter (best-effort: logged, never raised)."""
    if status != "done":
        return
    try:
        state = dict((await graph.aget_state(config)).values or {})
        slug = str(state.get("framework_slug") or "")
        chapter_id = str(state.get("chapter_id") or "")
        chapter_num = domains.dd.pipeline.runtime.dispatch.domain.chapter_number_from_id(chapter_id)
        chapter_stats = state.get("chapter_stats") or {}
        if not slug or not chapter_id or chapter_num <= 0 or not chapter_stats:
            return
        observability = domains.dd.pipeline.observability.service
        wall_ms = int(chapter_stats.get("wall_ms", 0) or 0)
        refine_iter = int(state.get("refine_iter", 0) or 0)
        halt_reason = str((state.get("mgsr_stats") or {}).get("halt_reason") or "")
        audit_passed = bool(chapter_stats.get("audit_passed", False))
        observability.record_chapter_outcome(
            outcome = "accept" if halt_reason == "chapter_passed" else "debt_below",
            framework = slug, chapter_number = chapter_num, duration_s = max(wall_ms / 1000.0, 0.0), iterations = max(refine_iter, 0),
        )
        n_code_refs = int(chapter_stats.get("n_code_refs", 0) or 0)
        n_missing = int(chapter_stats.get("n_missing", 0) or 0)
        missing_ratio = float(n_missing) / float(n_code_refs) if n_code_refs > 0 else (1.0 if not audit_passed else 0.0)
        observability.record_audit_missing(
            framework = slug, chapter_number = chapter_num, iteration = max(refine_iter, 0), missing_ratio = max(0.0, min(missing_ratio, 1.0)),
        )
    except Exception as error:
        logger.warning(f"[synth] terminal metric emit failed: {type(error).__name__}: {error}")


async def persist_chapter_timing(blobs, slug: str, chapter_id: str, wall_ms: int) -> None:
    """Best-effort merge of one finished chapter's wall time into the study timing blob (a single-chapter ▶ run leaves the other chapters' times, the harmonize
    time and the session fields as they were)."""
    key = domains.dd.pipeline.storage.keys.study_timing_key(slug)
    try:
        prior = json.loads(await blobs.read_text(key))
    except Exception:
        prior = {}
    per_chapter_ms = {str(k): int(v) for k, v in (prior.get("per_chapter_ms") or {}).items()}
    per_chapter_ms[chapter_id] = int(wall_ms)
    harmonize_ms = int(prior.get("harmonize_ms") or 0)
    payload = {
        "slug": slug,
        "total_wall_ms": sum(per_chapter_ms.values()) + harmonize_ms,
        "per_chapter_ms": per_chapter_ms,
        "harmonize_ms": harmonize_ms,
        "session_wall_ms": int(prior.get("session_wall_ms") or wall_ms),
        "finished_ts": time.time(),
    }
    try:
        await blobs.write(key, json.dumps(payload, indent = 2), content_type = "application/json")
    except Exception as error:
        logger.warning(f"[synth] {slug}/{chapter_id}: chapter timing persist failed ({type(error).__name__}: {error})")
