"""Async orchestration: per-chapter, resume, study-loop, book_harmonize.
Mirrors `domains/dd/planner/dispatch/service.py` structurally.

Three runners + one orchestrator + one harmonize helper:
  - run_single_chapter_async(thread_id, slug, chapter_id, mode):
        per-chapter graph.ainvoke + cancel watcher; terminal SSE.
  - resume_synth_async(thread_id):
        resume from last checkpoint (standard / catch-up / nothing).
  - run_study_async(study_thread_id, slug, chapter_ids, mode):
        strict-order chapter loop + per-chapter `chapter_ready` SSE +
        post-study book_harmonize pass.

book_harmonize is co-located so the Celery study task can invoke it before
returning (single-process post-study coherence pass).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import redis.asyncio as redis_aio

from ...ingestion.storage import get_storage
from ...resolver import index_by_slug
from ..book_harmonize import (
    compute_harmonize_manifest_hash,
    harmonize_book,
)
from ..cancel import clear_cancel, is_cancelled, watcher as cancel_watcher
from ..graph import NODE_REGISTRY, build_graph
from ..keys import (
    active_study_key,
    book_harmonize_latest_key,
    book_harmonize_versioned_key,
    chapter_challenges_key,
    chapter_flashcards_key,
    chapter_readme_key,
    chapter_render_latest_key,
    redis_url,
    study_timing_key,
)
from ..params import REDIS_CONNECT_TIMEOUT_S, REDIS_OP_TIMEOUT_S, STUDY_SEM
from ..progress import emit_progress
from .domain import missing_implemented_nodes
from .params import BOOK_HARMONIZE_MIN_CHAPTERS, CHAPTER_THREAD_PREFIX


logger = logging.getLogger(__name__)


async def _await_with_watcher(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
) -> dict:
    """Await main_task, write terminal status, cancel watcher, emit SSE."""
    terminal_patch: dict = {}
    try:
        await main_task
        terminal_patch["status"] = "done"
        logger.info(f"[synth] {thread_id}: done")
    except asyncio.CancelledError:
        terminal_patch["status"] = "cancelled"
        logger.info(f"[synth] {thread_id}: cancelled by user")
    except Exception as e:
        terminal_patch["status"] = "failed"
        terminal_patch["error"] = f"{type(e).__name__}: {e}"
        logger.exception(
            f"[synth] {thread_id}: run failed ({type(e).__name__}: {e})"
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
            f"[synth] {thread_id}: aupdate_state failed for terminal "
            f"patch {terminal_patch!r}: {type(e).__name__}: {e}"
        )

    await emit_progress(
        thread_id, "synth", "terminal",
        status = terminal_patch.get("status", "unknown"),
        error = terminal_patch.get("error"),
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
    }


async def run_single_chapter_async(
    thread_id: str,
    slug: str,
    chapter_id: str,
    mode: str = "quality",
) -> dict:
    """Fresh per-chapter run. Builds initial state + graph, spawns cancel
    watcher, awaits terminal."""
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
        "chapter_id":     chapter_id,
        "thread_id":      thread_id,
        "synth_mode":     mode,
        "status":         "running",
    }

    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
    )


async def run_missing_nodes_async(
    thread_id: str,
    missing: list[str],
) -> dict:
    """Catch-up — invoke missing IMPLEMENTED nodes via NODE_REGISTRY directly.
    Needed when a thread reached END BEFORE a new IMPLEMENTED node was added
    (ainvoke(None) would short-circuit the consumed END marker)."""
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
                f"[synth] {thread_id}: catch-up ran missing node "
                f"{name!r} → fields {sorted(result.keys())}"
            )
    except Exception as e:
        terminal_patch = {"status": "failed",
                          "error": f"{type(e).__name__}: {e}"}
        logger.exception(
            f"[synth] {thread_id}: catch-up failed: {type(e).__name__}: {e}"
        )

    try:
        await graph.aupdate_state(config, terminal_patch)
    except Exception as e:
        logger.warning(
            f"[synth] {thread_id}: aupdate_state failed for catch-up "
            f"terminal patch {terminal_patch!r}: {type(e).__name__}: {e}"
        )

    await emit_progress(
        thread_id, "synth", "terminal",
        status = terminal_patch.get("status", "unknown"),
        error = terminal_patch.get("error"),
    )

    return {
        "thread_id": thread_id,
        "status": terminal_patch.get("status", "unknown"),
        "error": terminal_patch.get("error"),
    }


async def resume_synth_async(thread_id: str) -> dict:
    """Resume from last checkpoint. Three sub-paths handled inline."""
    graph = build_graph()
    config = {"configurable": {"thread_id": thread_id}}

    snap = await graph.aget_state(config)
    if snap.values == {}:
        return {
            "thread_id": thread_id,
            "status": "failed",
            "error": (
                f"no checkpoints found for thread_id = {thread_id!r}; "
                f"call POST /synth/{{slug}} to start a fresh run"
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
                thread_id, "synth", "catch_up",
                missing = missing,
            )
            try:
                await graph.aupdate_state(config, {"status": "running"})
            except Exception as e:
                logger.warning(
                    f"[synth] {thread_id}: pre-catch-up status reset "
                    f"failed: {type(e).__name__}: {e}"
                )
            return await run_missing_nodes_async(thread_id, missing)
        await emit_progress(
            thread_id, "synth", "terminal",
            status = "done", error = None,
        )
        return {
            "thread_id": thread_id,
            "status": "done",
            "error": None,
        }

    await emit_progress(
        thread_id, "synth", "resumed",
        next_nodes = list(snap.next or []),
    )
    main_task = asyncio.create_task(graph.ainvoke(None, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    return await _await_with_watcher(
        graph, config, main_task, watcher_task, thread_id,
    )


async def _run_book_harmonize(
    *,
    slug: str,
    study_thread_id: str,
    chapter_ids: list[str],
) -> dict:
    """Post-study cross-chapter coherence pass. Loads each README.md,
    runs harmonize_book(), overwrites validated patches. Content-addressed
    cache skips work on identical inputs. Returns telemetry dict."""
    minio = get_storage()
    entry = index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug

    chapters: list[dict] = []
    skipped_missing: list[str] = []
    for cid in chapter_ids:
        key = chapter_readme_key(slug, cid)
        try:
            blob = await minio.read_bytes(key)
        except Exception:
            skipped_missing.append(cid)
            continue
        chapters.append({
            "chapter_id": cid,
            "title":      cid,
            "prose":      blob.decode("utf-8", errors = "replace"),
        })

    if len(chapters) < BOOK_HARMONIZE_MIN_CHAPTERS:
        await emit_progress(
            study_thread_id, "study", "book_harmonize_skipped",
            reason = "fewer_than_2_rendered_chapters",
            n_rendered = len(chapters),
        )
        return {
            "skipped": "fewer_than_2_rendered_chapters",
            "n_rendered_chapters": len(chapters),
            "missing_chapters": skipped_missing,
        }

    manifest_hash = compute_harmonize_manifest_hash(chapters)
    cache_key = book_harmonize_versioned_key(slug, manifest_hash)
    latest_key = book_harmonize_latest_key(slug)
    if await minio.exists(cache_key):
        try:
            cached_blob = await minio.read_bytes(cache_key)
            cached = json.loads(cached_blob.decode("utf-8"))
            cached["cache_hit"] = True
            cached["manifest_hash"] = manifest_hash
            await emit_progress(
                study_thread_id, "study", "book_harmonize_done",
                n_chapters = cached.get("n_chapters", 0),
                n_atomic_claims = cached.get("n_atomic_claims", 0),
                n_canonical_terms = cached.get("n_canonical_terms", 0),
                n_chapters_with_issues = cached.get(
                    "n_chapters_with_issues", 0,
                ),
                n_chapters_patched = cached.get("n_chapters_patched", 0),
                n_chapters_overwritten = cached.get(
                    "n_chapters_overwritten", 0,
                ),
                elapsed_ms = cached.get("elapsed_ms", 0),
                cache_hit = True,
            )
            logger.info(
                f"[book_harmonize] {slug}: CACHE HIT — manifest_hash = "
                f"{manifest_hash}"
            )
            return cached
        except Exception as e:
            logger.warning(
                f"[book_harmonize] cache read failed at {cache_key!r}: "
                f"{type(e).__name__}: {e} — recomputing"
            )

    await emit_progress(
        study_thread_id, "study", "book_harmonize_start",
        n_chapters = len(chapters),
    )

    try:
        # harmonize_book requires framework_slug (canonical-terms blob paths);
        # earlier BU/CC studies crashed when this was omitted.
        result = await harmonize_book(
            framework_slug = slug,
            framework_name = framework_name,
            chapters = chapters,
        )
    except Exception as e:
        logger.warning(
            f"[book_harmonize] {slug}: harmonize_book crashed "
            f"({type(e).__name__}: {e})"
        )
        return {
            "skipped": f"crash: {type(e).__name__}",
            "error": str(e)[:240],
        }

    n_overwritten = 0
    for patched in (result.get("patched_chapters") or []):
        cid = patched.get("chapter_id")
        new_prose = patched.get("new_prose")
        if not cid or not new_prose:
            continue
        try:
            await minio.write(
                chapter_readme_key(slug, cid),
                new_prose,
                content_type = "text/markdown",
            )
            n_overwritten += 1
        except Exception as e:
            logger.warning(
                f"[book_harmonize] {slug}/{cid}: overwrite failed "
                f"({type(e).__name__}: {e})"
            )

    payload = {
        **result,
        "manifest_hash":         manifest_hash,
        "n_chapters_overwritten": n_overwritten,
        "cache_hit":             False,
    }
    try:
        blob = json.dumps(payload, ensure_ascii = False).encode("utf-8")
        await minio.write(cache_key, blob, content_type = "application/json")
        await minio.write(latest_key, blob, content_type = "application/json")
    except Exception as e:
        logger.warning(
            f"[book_harmonize] {slug}: cache write failed "
            f"({type(e).__name__}: {e})"
        )

    await emit_progress(
        study_thread_id, "study", "book_harmonize_done",
        n_chapters = payload.get("n_chapters", 0),
        n_atomic_claims = payload.get("n_atomic_claims", 0),
        n_canonical_terms = payload.get("n_canonical_terms", 0),
        n_chapters_with_issues = payload.get("n_chapters_with_issues", 0),
        n_chapters_patched = payload.get("n_chapters_patched", 0),
        n_chapters_overwritten = n_overwritten,
        elapsed_ms = payload.get("elapsed_ms", 0),
        cache_hit = False,
    )
    return payload


def make_thread_id(slug: str) -> str:
    """Per-chapter thread_id; JS-side pre-generation uses the same format."""
    return f"{CHAPTER_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


def make_study_thread_id(slug: str) -> str:
    """Per-study orchestrator thread_id; distinct prefix from per-chapter
    so SQL/Redis pattern-matchers can tell them apart."""
    return f"{STUDY_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


async def _study_cancelled(study_thread_id: str) -> bool:
    """Per-study cancel flag set via `/synth/{study_thread_id}/cancel`."""
    r = redis_aio.from_url(
        redis_url(),
        socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
        socket_timeout = REDIS_OP_TIMEOUT_S,
    )
    try:
        return await is_cancelled(r, study_thread_id)
    except Exception:
        return False
    finally:
        try: await r.aclose()
        except Exception: pass


async def _persist_study_timing(
    slug: str,
    *,
    per_chapter_ms: dict[str, int],
    harmonize_ms: int,
    session_wall_ms: int,
    finished_ts: float,
) -> None:
    """Best-effort write of the study timing blob. Total = cumulative
    per-chapter wall + book_harmonize (resume-stable; session wall under-
    counts on a resume that skips rendered chapters)."""
    total = sum(int(v) for v in per_chapter_ms.values()) + int(harmonize_ms)
    payload = {
        "slug":            slug,
        "total_wall_ms":   total,
        "per_chapter_ms":  {k: int(v) for k, v in per_chapter_ms.items()},
        "harmonize_ms":    int(harmonize_ms),
        "session_wall_ms": int(session_wall_ms),
        "finished_ts":     finished_ts,
    }
    try:
        await get_storage().write(
            study_timing_key(slug),
            json.dumps(payload, indent = 2),
            content_type = "application/json",
        )
    except Exception as e:
        logger.warning(
            f"[study-orchestrator] {slug}: timing persist failed "
            f"({type(e).__name__}: {e})"
        )


async def run_study_async(
    study_thread_id: str,
    slug: str,
    chapter_ids: list[str],
    mode: str = "quality",
) -> dict:
    """Strict-order study orchestrator (Bundle 6 streaming).

    Iterates chapter_ids in pedagogical order; each chapter completes before
    the next starts. Emits `chapter_running` / `chapter_done` / `chapter_ready`
    so the UI surfaces each chapter on render_audit_write completion
    (TTFR ~10-15 min vs ~2h). Runs `book_harmonize` post-loop if ≥2 done.
    """
    n_total = len(chapter_ids)
    study_t0 = time.monotonic()
    # Seed per-chapter timing from a prior blob so SKIPPED chapters this
    # run keep their previously-measured time instead of dropping to 0 on resume.
    chapter_ms: dict[str, int] = {}
    try:
        _prior = json.loads(await get_storage().read_text(study_timing_key(slug)))
        chapter_ms.update(
            {str(k): int(v)
             for k, v in (_prior.get("per_chapter_ms") or {}).items()}
        )
    except Exception:
        pass
    await emit_progress(
        study_thread_id, "study", "study_start",
        slug = slug,
        n_chapters = n_total,
        chapter_ids = chapter_ids,
        mode = mode,
        concurrency = STUDY_SEM,
    )

    counters = {"completed": 0, "failed": 0, "cancelled": False}
    sem = asyncio.Semaphore(STUDY_SEM)

    async def _run_one(position: int, chapter_id: str) -> None:
        if await _study_cancelled(study_thread_id):
            counters["cancelled"] = True
            return

        # RESUME — skip chapters whose render-latest.json already exists.
        # Wipe Synth deletes render-latest.json, so post-wipe re-renders all.
        try:
            _minio = get_storage()
            if await _minio.exists(
                chapter_render_latest_key(slug, chapter_id),
            ):
                counters["completed"] += 1
                await emit_progress(
                    study_thread_id, "study", "chapter_done",
                    chapter_id = chapter_id, position = position, n_total = n_total,
                    status = "done", skipped = True,
                    wall_ms = chapter_ms.get(chapter_id, 0),
                )
                await emit_progress(
                    study_thread_id, "study", "chapter_ready",
                    chapter_id = chapter_id, position = position, n_total = n_total,
                    render_path = chapter_readme_key(slug, chapter_id),
                    challenges_path = chapter_challenges_key(slug, chapter_id),
                    flashcards_path = chapter_flashcards_key(slug, chapter_id),
                )
                logger.info(
                    f"[study-orchestrator] {slug}/{chapter_id}: "
                    f"SKIP (already rendered) ({position}/{n_total})"
                )
                return
        except Exception as e:
            logger.warning(
                f"[study-orchestrator] {slug}/{chapter_id}: resume-skip "
                f"check failed ({type(e).__name__}: {e}) — rendering anyway"
            )

        async with sem:
            if await _study_cancelled(study_thread_id):
                counters["cancelled"] = True
                return

            chapter_thread_id = make_thread_id(slug)
            ch_t0 = time.monotonic()
            await emit_progress(
                study_thread_id, "study", "chapter_running",
                chapter_id = chapter_id,
                chapter_thread_id = chapter_thread_id,
                position = position,
                n_total = n_total,
            )

            try:
                graph = build_graph()
            except RuntimeError as e:
                counters["failed"] += 1
                await emit_progress(
                    study_thread_id, "study", "chapter_done",
                    chapter_id = chapter_id,
                    position = position, n_total = n_total,
                    status = "failed", error = str(e),
                )
                return

            r = redis_aio.from_url(
                redis_url(),
                socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
                socket_timeout = REDIS_OP_TIMEOUT_S,
            )
            try:
                await clear_cancel(r, chapter_thread_id)
            finally:
                await r.aclose()

            initial_state = {
                "framework_slug": slug,
                "chapter_id":     chapter_id,
                "thread_id":      chapter_thread_id,
                "synth_mode":     mode,
                "status":         "running",
            }
            config = {"configurable": {"thread_id": chapter_thread_id}}

            main_task = asyncio.create_task(
                graph.ainvoke(initial_state, config),
            )
            watcher_task = asyncio.create_task(
                cancel_watcher(chapter_thread_id, main_task),
            )

            chapter_status = "done"
            chapter_error: str | None = None
            try:
                await main_task
            except asyncio.CancelledError:
                chapter_status = "cancelled"
            except Exception as e:
                chapter_status = "failed"
                chapter_error = f"{type(e).__name__}: {e}"
                logger.exception(
                    f"[study-orchestrator] {slug}/{chapter_id}: chapter "
                    f"run failed ({type(e).__name__}: {e})"
                )
            finally:
                watcher_task.cancel()
                try:
                    await watcher_task
                except (asyncio.CancelledError, Exception):
                    pass

            try:
                await graph.aupdate_state(
                    config,
                    {"status": chapter_status, "error": chapter_error},
                )
            except Exception as e:
                logger.warning(
                    f"[study-orchestrator] {slug}/{chapter_id}: "
                    f"aupdate_state failed: {type(e).__name__}: {e}"
                )

            await emit_progress(
                chapter_thread_id, "synth", "terminal",
                status = chapter_status, error = chapter_error,
            )

            ch_wall_ms = int((time.monotonic() - ch_t0) * 1000)
            if chapter_status == "done":
                counters["completed"] += 1
                chapter_ms[chapter_id] = ch_wall_ms
            else:
                counters["failed"] += 1
            await emit_progress(
                study_thread_id, "study", "chapter_done",
                chapter_id = chapter_id,
                chapter_thread_id = chapter_thread_id,
                position = position,
                n_total = n_total,
                status = chapter_status,
                error = chapter_error,
                wall_ms = ch_wall_ms,
            )
            if chapter_status == "done":
                await emit_progress(
                    study_thread_id, "study", "chapter_ready",
                    chapter_id = chapter_id,
                    chapter_thread_id = chapter_thread_id,
                    position = position,
                    n_total = n_total,
                    wall_ms = ch_wall_ms,
                    render_path = chapter_readme_key(slug, chapter_id),
                    challenges_path = chapter_challenges_key(slug, chapter_id),
                    flashcards_path = chapter_flashcards_key(slug, chapter_id),
                )
            logger.info(
                f"[study-orchestrator] {slug}/{chapter_id}: "
                f"{chapter_status} ({position}/{n_total})"
            )

    for i, cid in enumerate(chapter_ids):
        try:
            await _run_one(i + 1, cid)
        except Exception as e:
            counters["failed"] += 1
            logger.error(
                f"[study-orchestrator] {slug}: chapter {cid} task raised "
                f"unexpectedly: {type(e).__name__}: {e}"
            )
            continue
        if counters.get("cancelled"):
            break

    n_completed = counters["completed"]
    n_failed = counters["failed"]
    cancelled = counters["cancelled"]
    final_status = (
        "cancelled" if cancelled
        else ("failed" if n_failed and not n_completed else "done")
    )

    harmonize_stats: dict | None = None
    if (
        not cancelled
        and n_completed >= BOOK_HARMONIZE_MIN_CHAPTERS
        and final_status != "failed"
    ):
        try:
            harmonize_stats = await _run_book_harmonize(
                slug = slug,
                study_thread_id = study_thread_id,
                chapter_ids = chapter_ids,
            )
        except Exception as e:
            logger.warning(
                f"[study-orchestrator] {slug}: book_harmonize crashed "
                f"({type(e).__name__}: {e}) — proceeding without it"
            )
            harmonize_stats = {"skipped": f"crash: {type(e).__name__}"}

    # Hybrid timing roll-up — persisted so it survives refresh + shows on
    # cached studies; navbar total = cumulative chapter wall + harmonize.
    harmonize_ms = int((harmonize_stats or {}).get("elapsed_ms", 0) or 0)
    session_wall_ms = int((time.monotonic() - study_t0) * 1000)
    total_wall_ms = sum(int(v) for v in chapter_ms.values()) + harmonize_ms
    await _persist_study_timing(
        slug,
        per_chapter_ms = chapter_ms,
        harmonize_ms = harmonize_ms,
        session_wall_ms = session_wall_ms,
        finished_ts = time.time(),
    )

    await emit_progress(
        study_thread_id, "study", "study_done",
        n_completed = n_completed,
        n_failed = n_failed,
        n_total = n_total,
        final_status = final_status,
        book_harmonize = harmonize_stats,
        total_wall_ms = total_wall_ms,
        harmonize_ms = harmonize_ms,
        session_wall_ms = session_wall_ms,
        per_chapter_ms = chapter_ms,
    )
    # Mirror to `synth` terminal so EventSource handlers close cleanly.
    await emit_progress(
        study_thread_id, "synth", "terminal",
        status = final_status,
        error = None,
        n_completed = n_completed,
        n_failed = n_failed,
        n_total = n_total,
    )
    # Clear the live-run registry so a refresh doesn't reconnect to a finished study.
    try:
        _rc = redis_aio.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout = REDIS_OP_TIMEOUT_S,
        )
        try:
            await _rc.delete(active_study_key(slug))
        finally:
            await _rc.aclose()
    except Exception as e:
        logger.warning(
            f"[study-orchestrator] {slug}: active-run clear failed: "
            f"{type(e).__name__}: {e}"
        )
    logger.info(
        f"[study-orchestrator] {slug}: done — "
        f"{n_completed}/{n_total} completed, {n_failed} failed, "
        f"final_status = {final_status}"
    )
    return {
        "thread_id":      study_thread_id,
        "slug":           slug,
        "n_completed":    n_completed,
        "n_failed":       n_failed,
        "n_total":        n_total,
        "final_status":   final_status,
        "book_harmonize": harmonize_stats,
    }
