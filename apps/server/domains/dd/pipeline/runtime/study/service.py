"""The synth's study: a framework's chapters run through the chapter graph in strict order, each rendered chapter announced as it completes, then the cross-chapter
`book_harmonize` pass; the cancel and resume plumbing, the timing roll-up and the metrics around them.

The worker's composition root (`domains/dd/pipeline/ai/synth`) binds the graph, object storage and run context to COELHO Cloud and hands them in as a `Binding`."""
from __future__ import annotations
import ai, domains

import asyncio
import json
import logging
import time


logger = logging.getLogger(__name__)


async def run(binding: domains.dd.pipeline.runtime.runner.entities.Binding, study_thread_id: str, slug: str, chapter_ids: list[str], mode: str = "quality") -> dict:
    """Strict-order study orchestrator; emits `chapter_ready` per render so the UI shows each chapter as it completes (the first readable chapter in ~10-15 min
    instead of ~2h for the batch). Runs book_harmonize after the loop if at least two chapters are done."""
    with domains.dd.pipeline.observability.service.workflow_span(
        "dd.synth.study.run",
        workflow = "dd_synth",
        session_name = "dd",
        session_fields = {"session_id": study_thread_id, "user_id": slug, "study_id": study_thread_id, "framework": slug},
        attributes = {
            "dd.domain": "synth",
            "dd.run.kind": "study",
            "study.thread_id": study_thread_id,
            "study.framework_slug": slug,
            "study.mode": mode,
            "study.chapter_count": len(chapter_ids),
        },
        input_data = {
            "framework_slug": slug, "mode": mode, "requested_chapter_count": len(chapter_ids), "chapter_ids_preview": chapter_ids[:5], "thread_id": study_thread_id,
        },
        trace_metadata = {
            "pipeline": "dd_synth", "run_kind": "study", "framework_slug": slug, "mode": mode, "thread_id": study_thread_id, "requested_chapter_count": len(chapter_ids),
        },
        observation_metadata = {"framework_slug": slug, "mode": mode, "requested_chapter_count": len(chapter_ids)},
    ) as set_output:
        result = await _study(binding, study_thread_id, slug, chapter_ids, mode)
        harmonize = result.get("book_harmonize") or {}
        set_output({
            "status": result.get("final_status", "unknown"),
            "framework_slug": slug,
            "mode": mode,
            "requested_chapter_count": result.get("n_total", len(chapter_ids)),
            "completed_chapter_count": result.get("n_completed", 0),
            "failed_chapter_count": result.get("n_failed", 0),
            "harmonize_status": ("done" if harmonize and not harmonize.get("skipped") else (harmonize.get("skipped") or "not_run")),
        })
        return result


async def prior_render_verdict(blobs, slug: str, chapter_id: str, plan_chapters: dict[str, dict]) -> bool:
    """Whether a chapter already holds a render that a resumed study must not redo: it exists, its audit passed and it was built under the current plan."""
    render_key = domains.dd.pipeline.storage.keys.chapter_render_latest_key(
        slug, chapter_id, ai.dd.graphs.pipeline.synth.params.version(),
    )
    if not await blobs.exists(render_key):
        return False
    try:
        render = json.loads(await blobs.read_text(render_key))
    except Exception as error:
        logger.warning(f"[study-orchestrator] {slug}/{chapter_id}: prior render blob unreadable for the resume-skip check ({type(error).__name__}: {error}) — rendering anyway")
        return False
    if not bool((render.get("audit") or {}).get("audit_passed", True)):
        # A prior run left content whose audit FAILED: resuming must not treat that as "already done" forever.
        logger.info(f"[study-orchestrator] {slug}/{chapter_id}: prior render failed its audit — not skipping, re-running")
        return False
    if ai.dd.nodes.pipeline.synth.render.domain.render_is_stale(render, plan_chapters.get(chapter_id)):
        # Built under an older plan (a re-plan changed this chapter's title/sources): never "already done".
        logger.info(f"[study-orchestrator] {slug}/{chapter_id}: prior render belongs to an older plan — not skipping, re-running")
        return False
    return True


async def _execute_chapter(
    binding: domains.dd.pipeline.runtime.runner.entities.Binding, study_thread_id: str, slug: str, chapter_id: str, chapter_thread_id: str, mode: str,
) -> tuple[str, str | None]:
    """Run one chapter's graph inside the study and report how it ended: (`done` | `needs_review` | `failed` | `cancelled`, error)."""
    graph = binding.graph
    config = binding.config(chapter_thread_id)
    await domains.dd.pipeline.runtime.cancel.service.reset(chapter_thread_id)
    await domains.dd.pipeline.runtime.dispatch.service.track_chapter(study_thread_id, chapter_thread_id)

    initial_state = {
        "framework_slug": slug, "chapter_id": chapter_id, "thread_id": chapter_thread_id, "synth_mode": mode, "status": "running", "run_started_at": time.time(),
    }
    main_task, watcher_task = domains.dd.pipeline.runtime.runner.service.launch(binding, initial_state, config, chapter_thread_id)
    chapter_status = "done"
    chapter_error: str | None = None
    try:
        await main_task
        # The graph running to its end is not the chapter being fit to ship: render signals a failed audit through the state's status, not an exception.
        values = dict((await graph.aget_state(config)).values or {})
        if values.get("status") == "audit_failed":
            chapter_status = "needs_review"
            chapter_error = values.get("error") or "render audit failed — content persisted for review but not marked done"
        elif values.get("status") == "failed":
            chapter_status = "failed"
            chapter_error = values.get("error")
    except asyncio.CancelledError:
        chapter_status = "cancelled"
    except Exception as error:
        chapter_status = "failed"
        chapter_error = f"{type(error).__name__}: {error}"
        logger.exception(f"[study-orchestrator] {slug}/{chapter_id}: chapter run failed ({type(error).__name__}: {error})")
    finally:
        await domains.dd.pipeline.runtime.runner.service.stop_watcher(watcher_task)
        await domains.dd.pipeline.runtime.dispatch.service.untrack_chapter(study_thread_id, chapter_thread_id)

    try:
        await graph.aupdate_state(config, {"status": chapter_status, "error": chapter_error})
    except Exception as error:
        logger.warning(f"[study-orchestrator] {slug}/{chapter_id}: aupdate_state failed: {type(error).__name__}: {error}")
    await domains.dd.pipeline.observability.service.record_chapter_terminal(graph, config, chapter_status)
    await domains.dd.pipeline.runtime.runner.service.snapshot_usage(chapter_thread_id, "study-orchestrator")
    await domains.dd.pipeline.runtime.progress.service.emit_progress(chapter_thread_id, "synth", "terminal", status = chapter_status, error = chapter_error)
    return chapter_status, chapter_error


async def _study(binding: domains.dd.pipeline.runtime.runner.entities.Binding, study_thread_id: str, slug: str, chapter_ids: list[str], mode: str) -> dict:
    n_total = len(chapter_ids)
    study_t0 = time.monotonic()
    blobs = binding.blobs
    await domains.dd.pipeline.runtime.synth.service.ensure_corpus_current(slug)
    chapter_ms = await domains.dd.pipeline.storage.service.read_study_chapter_ms(blobs, slug)
    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        study_thread_id, "study", "study_start",
        slug = slug, n_chapters = n_total, chapter_ids = chapter_ids, mode = mode, concurrency = ai.dd.graphs.pipeline.synth.params.study_sem(),
    )

    counters = {"completed": 0, "needs_review": 0, "failed": 0, "cancelled": False}
    sem = asyncio.Semaphore(ai.dd.graphs.pipeline.synth.params.study_sem())
    plan_chapters = await domains.dd.pipeline.storage.service.read_plan_chapters(blobs, slug)

    async def run_one(position: int, chapter_id: str) -> None:
        if await domains.dd.pipeline.runtime.cancel.service.requested(study_thread_id):
            counters["cancelled"] = True
            return
        try:
            if await prior_render_verdict(blobs, slug, chapter_id, plan_chapters):
                counters["completed"] += 1
                await domains.dd.pipeline.runtime.progress.service.emit_progress(
                    study_thread_id, "study", "chapter_done",
                    chapter_id = chapter_id, position = position, n_total = n_total, status = "done", skipped = True, wall_ms = chapter_ms.get(chapter_id, 0),
                )
                await domains.dd.pipeline.runtime.progress.service.emit_progress(
                    study_thread_id, "study", "chapter_ready",
                    chapter_id = chapter_id, position = position, n_total = n_total, render_path = domains.dd.pipeline.storage.keys.chapter_readme_key(slug, chapter_id),
                )
                logger.info(f"[study-orchestrator] {slug}/{chapter_id}: SKIP (already rendered) ({position}/{n_total})")
                return
        except Exception as error:
            logger.warning(f"[study-orchestrator] {slug}/{chapter_id}: resume-skip check failed ({type(error).__name__}: {error}) — rendering anyway")

        async with sem:
            if await domains.dd.pipeline.runtime.cancel.service.requested(study_thread_id):
                counters["cancelled"] = True
                return
            chapter_thread_id = domains.dd.pipeline.runtime.dispatch.domain.make_chapter_thread_id(slug)
            ch_t0 = time.monotonic()
            await domains.dd.pipeline.runtime.progress.service.emit_progress(
                study_thread_id, "study", "chapter_running",
                chapter_id = chapter_id, chapter_thread_id = chapter_thread_id, position = position, n_total = n_total,
            )
            chapter_status, chapter_error = await _execute_chapter(binding, study_thread_id, slug, chapter_id, chapter_thread_id, mode)

            ch_wall_ms = int((time.monotonic() - ch_t0) * 1000)
            if chapter_status == "done":
                counters["completed"] += 1
                chapter_ms[chapter_id] = ch_wall_ms
            elif chapter_status == "needs_review":
                counters["needs_review"] += 1
            else:
                counters["failed"] += 1
            await domains.dd.pipeline.runtime.progress.service.emit_progress(
                study_thread_id, "study", "chapter_done",
                chapter_id = chapter_id, chapter_thread_id = chapter_thread_id, position = position, n_total = n_total,
                status = chapter_status, error = chapter_error, wall_ms = ch_wall_ms,
            )
            if chapter_status == "done":
                await domains.dd.pipeline.runtime.progress.service.emit_progress(
                    study_thread_id, "study", "chapter_ready",
                    chapter_id = chapter_id, chapter_thread_id = chapter_thread_id, position = position, n_total = n_total, wall_ms = ch_wall_ms,
                    render_path = domains.dd.pipeline.storage.keys.chapter_readme_key(slug, chapter_id),
                )
            logger.info(f"[study-orchestrator] {slug}/{chapter_id}: {chapter_status} ({position}/{n_total})")

    for i, cid in enumerate(chapter_ids):
        try:
            await run_one(i + 1, cid)
        except Exception as error:
            counters["failed"] += 1
            logger.error(f"[study-orchestrator] {slug}: chapter {cid} task raised unexpectedly: {type(error).__name__}: {error}")
            continue
        if counters.get("cancelled"):
            break

    n_completed, n_needs_review, n_failed, cancelled = counters["completed"], counters["needs_review"], counters["failed"], counters["cancelled"]
    final_status = (
        "cancelled" if cancelled
        else "failed" if n_failed and not n_completed
        # A study isn't cleanly "done" while any chapter shipped with a failed render audit: surface it distinctly rather than reporting blanket success.
        else "needs_review" if n_needs_review
        else "done"
    )

    harmonize_stats: dict | None = None
    if not cancelled and n_completed >= ai.dd.nodes.pipeline.synth.book_harmonize.params.MIN_CHAPTERS and final_status != "failed":
        try:
            harmonize_stats = await ai.dd.nodes.pipeline.synth.book_harmonize.service.run_pass(
                binding.context(), slug = slug, study_thread_id = study_thread_id, chapter_ids = chapter_ids,
            )
        except Exception as error:
            logger.warning(f"[study-orchestrator] {slug}: book_harmonize crashed ({type(error).__name__}: {error}) — proceeding without it")
            harmonize_stats = {"skipped": f"crash: {type(error).__name__}"}

    harmonize_ms = int((harmonize_stats or {}).get("elapsed_ms", 0) or 0)
    session_wall_ms = int((time.monotonic() - study_t0) * 1000)
    total_wall_ms = sum(int(v) for v in chapter_ms.values()) + harmonize_ms
    await domains.dd.pipeline.storage.service.write_study_timing(
        blobs, slug, per_chapter_ms = chapter_ms, harmonize_ms = harmonize_ms, session_wall_ms = session_wall_ms, finished_ts = time.time(),
    )

    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        study_thread_id, "study", "study_done",
        n_completed = n_completed, n_needs_review = n_needs_review, n_failed = n_failed, n_total = n_total, final_status = final_status,
        book_harmonize = harmonize_stats, total_wall_ms = total_wall_ms, harmonize_ms = harmonize_ms, session_wall_ms = session_wall_ms, per_chapter_ms = chapter_ms,
    )
    # Mirror to the `synth` terminal so EventSource handlers close cleanly.
    await domains.dd.pipeline.runtime.progress.service.emit_progress(
        study_thread_id, "synth", "terminal",
        status = final_status, error = None, n_completed = n_completed, n_needs_review = n_needs_review, n_failed = n_failed, n_total = n_total,
    )
    await domains.dd.pipeline.runtime.dispatch.service.clear_active_study(slug)
    logger.info(f"[study-orchestrator] {slug}: done — {n_completed}/{n_total} completed, {n_failed} failed, final_status = {final_status}")
    try:
        domains.dd.pipeline.observability.service.record_study_completion(
            framework = slug, duration_s = max(total_wall_ms / 1000.0, 0.0), n_accepted = n_completed, n_total = n_total, outcome = final_status,
        )
    except Exception as error:
        logger.warning(f"[study-orchestrator] {slug}: study metric emit failed ({type(error).__name__}: {error})")
    return {
        "thread_id": study_thread_id,
        "slug": slug,
        "n_completed": n_completed,
        "n_failed": n_failed,
        "n_total": n_total,
        "final_status": final_status,
        "book_harmonize": harmonize_stats,
    }
