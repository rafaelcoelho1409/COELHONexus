"""Synth pipeline endpoints — per-chapter LangGraph runs.

Endpoint contract mirrors planner.py (so the FastHTML UI can use the
same SSE / poll / resume / cancel flows). Synth runs PER CHAPTER:
each chapter is its own thread_id + its own graph invocation.

Endpoints:

  GET  /synth/info
      → {node_order, implemented, modes}
  GET  /synth/recent
      → most-recent thread per slug for page-refresh recovery
        (chapter_id lives in state, NOT in the thread_id)
  POST /synth/{slug}?chapter_id=ch-..&mode=quality&thread_id=...
      → kick off a synth run for one chapter; returns thread_id +
        chapter_id. If chapter_id omitted, picks first chapter from
        `planner/{slug}/plan-latest.json`.
  POST /synth/{thread_id:path}/resume
      → resume from last checkpoint (LangGraph ainvoke(None))
  POST /synth/{thread_id:path}/cancel
      → cooperative cancel via Redis flag + asyncio.Task.cancel
  GET  /synth/{thread_id:path}/events
      → SSE stream of substep progress events
  GET  /synth/debug/graph/{thread_id:path}/state
      → latest LangGraph checkpoint values for the thread
  GET  /synth/debug/graph/{thread_id:path}/history
      → all checkpoints for the thread (debug)
  DELETE /synth/{slug}/wipe
      → delete MinIO synth/{slug}/ + Postgres checkpoints for the slug

thread_id format: `docs-distiller/synth/{slug}/{uuid}`
  (chapter_id is in SynthState, not in thread_id — see _make_thread_id)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from urllib.parse import quote

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException, Query
from starlette.responses import StreamingResponse

from domains.dd.ingestion.storage import get_storage
from domains.dd.synth.cancel import (
    _redis_url,
    clear_cancel,
    request_cancel,
    watcher as cancel_watcher,
)
from domains.dd.synth.graph import (
    IMPLEMENTED,
    NODE_ORDER,
    NODE_REGISTRY,
    NODE_TO_FIELD,
    build_graph,
)
from domains.dd.synth.progress import (
    emit_progress,
    subscribe_progress,
)


logger = logging.getLogger(__name__)


router = APIRouter()


# Strong refs to detached synth tasks so the event loop doesn't GC them
# mid-run. Each task removes itself on completion via add_done_callback.
_active_runs: set[asyncio.Task] = set()


# =============================================================================
# Info + recent
# =============================================================================
@router.get("/info")
async def synth_info() -> dict:
    """Catalog of synth substeps + which are wired. UI uses this to mark
    cards as "future" vs "ready"."""
    return {
        "node_order":  list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        "modes": [
            {"key": "quality", "label": "Quality (default)", "enabled": True},
            {"key": "fast",    "label": "Fast (3 iters)",    "enabled": False},
        ],
        "status": "live" if IMPLEMENTED else "scaffolding",
    }


# =============================================================================
# Step 5 Study viewer — artifact serving
# =============================================================================
# Per `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md` 5-step pipeline:
#   Catalog → Ingestion → Planner → Synth → Study
# Step 5 (Study) needs to read the 3 artifacts that render_audit_write
# produces per chapter:
#   - synth/{slug}/{chapter_id}/README.md       (full chapter markdown)
#   - synth/{slug}/{chapter_id}/challenges.md   (active-recall questions)
#   - synth/{slug}/{chapter_id}/flashcards.json (Q/A pairs)
# Plus a "list chapters with their render-status" endpoint so the
# sidebar can show which chapters are ready vs not-yet-synthesized.

_VALID_ARTIFACTS = {
    "README.md":       "text/markdown; charset=utf-8",
    "challenges.md":   "text/markdown; charset=utf-8",
    "flashcards.json": "application/json",
}


@router.get("/{slug}/study/chapters")
async def list_study_chapters(slug: str) -> dict:
    """Chapter list for the Step 5 study viewer. For each chapter in
    `planner/{slug}/plan-latest.json`, returns whether render_audit_write
    has produced its artifacts yet — drives the sidebar status badges.

    Shape:
      {
        "framework_slug": str,
        "chapters": [
          {
            "id":           "ch-01-introduction-to-pydantic-basics",
            "title":        "Introduction to Pydantic Basics",
            "order":        1,
            "n_sources":    9,
            "rendered":     true,
            "audit_passed": true,
            "render_path":  "synth/.../render-latest.json"  (when rendered)
          },
          ...
        ]
      }
    """
    plan = await _load_plan(slug)
    chapters_in: list[dict] = plan.get("chapters") or []
    if not chapters_in:
        return {"framework_slug": slug, "chapters": []}

    minio = get_storage()
    out: list[dict] = []
    for ch in chapters_in:
        cid = (ch or {}).get("id")
        if not cid:
            continue
        render_key = (
            f"synth/{slug}/{cid}/render-latest.json"
        )
        rendered = await minio.exists(render_key)
        entry: dict = {
            "id":         cid,
            "title":      ch.get("title") or cid,
            "order":      ch.get("order") or 0,
            "n_sources":  len(ch.get("sources") or []),
            "rendered":   rendered,
            "audit_passed": False,
            "render_path": render_key if rendered else None,
        }
        if rendered:
            try:
                text = await minio.read_text(render_key)
                rp = json.loads(text)
                entry["audit_passed"] = bool(
                    (rp.get("audit") or {}).get("audit_passed", False)
                )
                entry["rendered_chars"] = rp.get("rendered_chars", 0)
                entry["n_sections"] = rp.get("n_sections", 0)
                # Synth thread that produced this render — lets the UI
                # re-open the chapter's LangGraph canvas after a refresh.
                # May be absent on blobs written before this field shipped.
                entry["thread_id"] = rp.get("thread_id") or None
            except Exception:
                # render-latest exists but unparseable — flag as rendered
                # but audit unknown
                pass
        out.append(entry)
    return {"framework_slug": slug, "chapters": out}


@router.get("/{slug}/study/{chapter_id}/artifact/{artifact_name}")
async def get_study_artifact(
    slug: str, chapter_id: str, artifact_name: str,
) -> StreamingResponse:
    """Stream one of the 3 chapter artifacts back to the browser. Used
    by the Step 5 viewer to render README.md (via marked.js), display
    challenges.md (also via marked.js), and parse flashcards.json
    client-side.

    Names enforced via `_VALID_ARTIFACTS` allow-list so this endpoint
    can't be used to read arbitrary MinIO keys.
    """
    if artifact_name not in _VALID_ARTIFACTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid artifact name {artifact_name!r}; valid: "
                f"{sorted(_VALID_ARTIFACTS)}"
            ),
        )
    key = f"synth/{slug}/{chapter_id}/{artifact_name}"
    minio = get_storage()
    if not await minio.exists(key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"artifact {artifact_name!r} for chapter {chapter_id!r} "
                f"not in MinIO at {key!r}; run synth + render first"
            ),
        )

    async def _gen():
        try:
            text = await minio.read_text(key)
            yield text.encode("utf-8")
        except Exception as e:
            logger.warning(
                f"[synth-study-artifact] read failed for {key!r}: "
                f"{type(e).__name__}: {e}"
            )
            yield b""

    return StreamingResponse(
        _gen(),
        media_type=_VALID_ARTIFACTS[artifact_name],
        headers={
            "Cache-Control": "public, max-age=60",
        },
    )


@router.get("/recent")
async def list_recent_synth() -> dict:
    """Most-recent thread per slug. thread_id format:
    `docs-distiller/synth/{slug}/{uuid}` → split_part(thread_id, '/', 3)
    gives slug. chapter_id lives in state, NOT in the thread_id — see
    _make_thread_id rationale."""
    import psycopg

    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get(
        "POSTGRES_DATABASE", os.environ.get("POSTGRES_DB", "postgres"),
    )
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    out: list[dict] = []
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    WITH thread_stats AS (
                        SELECT
                            split_part(thread_id, '/', 3) AS slug,
                            thread_id,
                            count(*)           AS ckpt_count,
                            max(checkpoint_id) AS latest_ckpt
                        FROM checkpoints
                        WHERE thread_id LIKE 'docs-distiller/synth/%'
                        GROUP BY thread_id
                    )
                    SELECT DISTINCT ON (slug)
                        slug, thread_id, ckpt_count, latest_ckpt
                    FROM thread_stats
                    ORDER BY slug, ckpt_count DESC, latest_ckpt DESC
                """)
                for slug, tid, ckpt_count, latest in await cur.fetchall():
                    out.append({
                        "slug":          slug,
                        "thread_id":     tid,
                        "checkpoint_id": str(latest),
                        "ckpt_count":    int(ckpt_count),
                    })
    except Exception as e:
        logger.warning(f"[synth-recent] query failed: {e}")
    return {"recent": out}


# =============================================================================
# Background-runner wrapper
# =============================================================================
async def _run_synth_background(
    graph,
    config: dict,
    main_task: asyncio.Task,
    watcher_task: asyncio.Task,
    thread_id: str,
) -> None:
    """Await the synth graph task in the background, write terminal
    status + emit SSE terminal event. Mirrors the planner pattern."""
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
        status=terminal_patch.get("status", "unknown"),
        error=terminal_patch.get("error"),
    )


# =============================================================================
# Helpers
# =============================================================================
def _planner_latest_key(slug: str) -> str:
    return f"planner/{slug}/plan-latest.json"


async def _load_plan(slug: str) -> dict:
    minio = get_storage()
    plan_key = _planner_latest_key(slug)
    if not await minio.exists(plan_key):
        raise HTTPException(
            status_code=404,
            detail=(
                f"no planner plan for {slug!r} — run the planner first "
                f"(POST /planner/{slug})"
            ),
        )
    try:
        text = await minio.read_text(plan_key)
        return json.loads(text) or {}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"plan {plan_key!r} unreadable: {type(e).__name__}: {e}",
        )


def _pick_first_chapter_id(plan: dict) -> str | None:
    chapters = plan.get("chapters") or []
    for ch in chapters:
        cid = (ch or {}).get("id")
        if cid:
            return cid
    return None


_VALID_MODES = {"quality", "fast"}


def _make_thread_id(slug: str, chapter_id: str | None = None) -> str:
    """Canonical per-chapter synth thread_id. MUST match
    apps/fasthtml/static/js/docs_distiller.js:_genSynthThreadId so the
    Redis channel + /synth/recent SQL + /synth/{slug}/wipe SQL pattern-
    match correctly across client/server.

    `chapter_id` is intentionally NOT embedded in the thread_id: the JS
    pre-generates a thread_id for the Cancel button BEFORE the POST
    response (no "pending" dead-zone), and at that point it doesn't
    know which chapter the server will pick. The chapter_id lives in
    SynthState instead — recoverable via /debug/graph/{tid}/state."""
    return f"docs-distiller/synth/{slug}/{uuid.uuid4()}"


def _make_study_thread_id(slug: str) -> str:
    """Study orchestrator thread_id — distinct prefix so /synth/recent
    + /synth/{slug}/wipe SQL pattern-matchers can tell apart per-chapter
    runs from study-level orchestrator runs. Used as the SSE channel
    the UI subscribes to for orchestrator-level events (study_start,
    chapter_running, chapter_done, study_done)."""
    return f"docs-distiller/study/{slug}/{uuid.uuid4()}"


# =============================================================================
# Study orchestrator
# =============================================================================
# Sequential per-chapter runner. POST /synth/{slug} (no chapter_id) spawns
# this as a detached background task. For each chapter in the planner plan,
# it kicks off a normal per-chapter graph.ainvoke and waits for completion
# before moving to the next. The orchestrator emits study-level SSE events
# on its own thread_id so the UI can show a chapter-progress strip that
# updates in real time as chapters move pending → running → done.
#
# Concurrency: _STUDY_SEM = 1 (sequential). Bump to 2+ later if needed —
# free-tier rotator + bandit handle ~30 concurrent calls cleanly, but
# starting sequential makes the UX easier to follow.

_STUDY_SEM = 1


async def _run_book_harmonize(
    *,
    slug: str,
    study_thread_id: str,
    chapter_ids: list[str],
) -> dict:
    """Post-study cross-chapter coherence pass. Loads each chapter's rendered
    README.md from MinIO, runs harmonize_book(), overwrites any chapter whose
    patch passed validation. Content-addressed cache (Ship #5) skips work on
    identical README contents.

    Returns telemetry dict suitable for the study_done SSE payload."""
    from domains.dd.ingestion.storage import get_storage
    from domains.dd.resolver import _index_by_slug
    from domains.dd.synth.book_harmonize import (
        compute_harmonize_manifest_hash,
        harmonize_book,
    )

    minio = get_storage()

    # Resolve framework display name (best-effort)
    entry = _index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug

    # Load each chapter's rendered prose from MinIO. Skip any that
    # didn't render (failed chapters won't have README.md).
    chapters: list[dict] = []
    skipped_missing: list[str] = []
    for cid in chapter_ids:
        key = f"synth/{slug}/{cid}/README.md"
        try:
            blob = await minio.read_bytes(key)
        except Exception:
            skipped_missing.append(cid)
            continue
        chapters.append({
            "chapter_id": cid,
            "title":      cid,   # title can be enriched later from render-latest.json
            "prose":      blob.decode("utf-8", errors="replace"),
        })

    if len(chapters) < 2:
        await emit_progress(
            study_thread_id, "study", "book_harmonize_skipped",
            reason="fewer_than_2_rendered_chapters",
            n_rendered=len(chapters),
        )
        return {
            "skipped": "fewer_than_2_rendered_chapters",
            "n_rendered_chapters": len(chapters),
            "missing_chapters": skipped_missing,
        }

    # ── Ship #5: content-addressed cache ───────────────────────────────
    # Manifest hash is sha256 of (sorted chapter prose hashes + prompt
    # version + schema version). On re-run with identical chapter content,
    # this matches → skip the LLM work entirely. After a successful patch
    # the README hashes change → next run is a cache miss → harmonize re-
    # runs but finds no violations → writes new cache blob → third run is
    # a clean hit (idempotent convergence).
    manifest_hash = compute_harmonize_manifest_hash(chapters)
    cache_key = f"synth/{slug}/book_harmonize/{manifest_hash}.json"
    latest_key = f"synth/{slug}/book_harmonize-latest.json"
    if await minio.exists(cache_key):
        try:
            cached_blob = await minio.read_bytes(cache_key)
            cached = json.loads(cached_blob.decode("utf-8"))
            cached["cache_hit"] = True
            cached["manifest_hash"] = manifest_hash
            await emit_progress(
                study_thread_id, "study", "book_harmonize_done",
                n_chapters=cached.get("n_chapters", 0),
                n_atomic_claims=cached.get("n_atomic_claims", 0),
                n_canonical_terms=cached.get("n_canonical_terms", 0),
                n_chapters_with_issues=cached.get("n_chapters_with_issues", 0),
                n_chapters_patched=cached.get("n_chapters_patched", 0),
                n_chapters_overwritten=cached.get("n_chapters_overwritten", 0),
                elapsed_ms=cached.get("elapsed_ms", 0),
                cache_hit=True,
            )
            logger.info(
                f"[book_harmonize] {slug}: CACHE HIT — manifest_hash="
                f"{manifest_hash}, skipping LLM work"
            )
            return cached
        except Exception as e:
            logger.warning(
                f"[book_harmonize] cache read failed at {cache_key!r}: "
                f"{type(e).__name__}: {e} — recomputing"
            )

    await emit_progress(
        study_thread_id, "study", "book_harmonize_start",
        n_chapters=len(chapters),
        manifest_hash=manifest_hash,
    )

    result = await harmonize_book(
        framework_slug=slug,
        framework_name=framework_name,
        chapters=chapters,
    )

    # Overwrite README.md for chapters that produced a valid patch.
    n_overwritten = 0
    for patch in result.get("patches", []):
        if not patch.get("patched"):
            continue
        new_prose = patch.get("new_prose")
        if not new_prose:
            continue
        cid = patch["chapter_id"]
        key = f"synth/{slug}/{cid}/README.md"
        try:
            await minio.write(
                key, new_prose.encode("utf-8"),
                content_type="text/markdown",
            )
            n_overwritten += 1
        except Exception as e:
            logger.warning(
                f"[book_harmonize] {slug}/{cid}: overwrite failed "
                f"({type(e).__name__}: {e})"
            )

    await emit_progress(
        study_thread_id, "study", "book_harmonize_done",
        n_chapters=result.get("n_chapters", 0),
        n_atomic_claims=result.get("n_atomic_claims", 0),
        n_canonical_terms=result.get("n_canonical_terms", 0),
        n_chapters_with_issues=result.get("n_chapters_with_issues", 0),
        n_chapters_patched=result.get("n_chapters_patched", 0),
        n_chapters_overwritten=n_overwritten,
        elapsed_ms=result.get("elapsed_ms", 0),
    )
    logger.info(
        f"[book_harmonize] {slug}: "
        f"{n_overwritten}/{result.get('n_chapters_with_issues', 0)} chapters "
        f"overwritten with harmonized prose "
        f"({result.get('n_chapters', 0)} total, "
        f"{result.get('n_canonical_terms', 0)} canonical terms, "
        f"{result.get('elapsed_ms', 0)}ms)"
    )
    final = {
        **result,
        "n_chapters_overwritten": n_overwritten,
        "missing_chapters": skipped_missing,
        "manifest_hash": manifest_hash,
        "cache_hit": False,
    }
    # ── Persist cache for next restart ──────────────────────────────────
    try:
        blob = json.dumps(final, indent=2, ensure_ascii=False).encode("utf-8")
        await minio.write(cache_key, blob, content_type="application/json")
        await minio.write(latest_key, blob, content_type="application/json")
    except Exception as e:
        logger.warning(
            f"[book_harmonize] cache write failed at {cache_key!r}: "
            f"{type(e).__name__}: {e} (non-fatal)"
        )
    return final


async def _run_study_orchestrator(
    *,
    slug: str,
    study_thread_id: str,
    chapter_ids: list[str],
    mode: str,
) -> None:
    """Semaphore-gated orchestrator: run chapters through the synth graph.

    Concurrency = `_STUDY_SEM` (default 1 — functionally sequential).
    Bumping `_STUDY_SEM` enables true parallelism via `asyncio.gather`
    over per-chapter coroutines, each gated by a shared Semaphore.

    For each chapter:
      1. Mint a per-chapter thread_id
      2. Emit `chapter_running` on the study channel
      3. Run graph.ainvoke for the chapter (all 6 nodes fire as usual)
      4. On completion, emit `chapter_done` with status

    Cancellation (POST /synth/{study_thread_id}/cancel sets the cancel
    flag on the study thread):
      - Queued chapters waiting on the semaphore short-circuit on the
        cancel check before running
      - In-flight chapters complete naturally (their per-chapter cancel
        is independent — cancel each one to interrupt mid-pipeline)
    """
    n_total = len(chapter_ids)
    await emit_progress(
        study_thread_id, "study", "study_start",
        slug=slug,
        n_chapters=n_total,
        chapter_ids=chapter_ids,
        mode=mode,
        concurrency=_STUDY_SEM,
    )

    # asyncio is single-threaded — naked dict mutation is race-free.
    counters = {"completed": 0, "failed": 0, "cancelled": False}
    sem = asyncio.Semaphore(_STUDY_SEM)

    async def _study_cancelled() -> bool:
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            from domains.dd.synth.cancel import is_cancelled
            return await is_cancelled(r, study_thread_id)
        except Exception:
            return False
        finally:
            try: await r.aclose()
            except Exception: pass

    async def _run_one(position: int, chapter_id: str) -> None:
        # Fast pre-acquire cancel check — short-circuit deeply-queued
        # chapters when a cancel arrives before their turn.
        if await _study_cancelled():
            counters["cancelled"] = True
            return

        async with sem:
            # Re-check after acquiring the slot — a cancel may have
            # fired while we waited behind the semaphore.
            if await _study_cancelled():
                counters["cancelled"] = True
                return

            chapter_thread_id = _make_thread_id(slug)
            await emit_progress(
                study_thread_id, "study", "chapter_running",
                chapter_id=chapter_id,
                chapter_thread_id=chapter_thread_id,
                position=position,
                n_total=n_total,
            )

            try:
                graph = build_graph()
            except RuntimeError as e:
                counters["failed"] += 1
                await emit_progress(
                    study_thread_id, "study", "chapter_done",
                    chapter_id=chapter_id,
                    position=position, n_total=n_total,
                    status="failed", error=str(e),
                )
                return

            # Clear any stale cancel flag on the per-chapter thread.
            r = redis_aio.from_url(
                _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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

            # Patch terminal status into the per-chapter checkpointer.
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
                status=chapter_status, error=chapter_error,
            )

            if chapter_status == "done":
                counters["completed"] += 1
            else:
                counters["failed"] += 1
            await emit_progress(
                study_thread_id, "study", "chapter_done",
                chapter_id=chapter_id,
                chapter_thread_id=chapter_thread_id,
                position=position,
                n_total=n_total,
                status=chapter_status,
                error=chapter_error,
            )
            logger.info(
                f"[study-orchestrator] {slug}/{chapter_id}: "
                f"{chapter_status} ({position}/{n_total})"
            )

    # Schedule all chapters at once — the Semaphore caps the actual
    # in-flight count. With _STUDY_SEM=1 this is functionally sequential.
    # return_exceptions=True so one chapter's unexpected error can't
    # cancel its still-queued siblings (each _run_one is already
    # defensively wrapped, so this is belt-and-suspenders).
    results = await asyncio.gather(
        *[_run_one(i + 1, cid) for i, cid in enumerate(chapter_ids)],
        return_exceptions=True,
    )
    for res in results:
        if isinstance(res, Exception):
            logger.error(
                f"[study-orchestrator] {slug}: a chapter task raised "
                f"unexpectedly: {type(res).__name__}: {res}"
            )

    n_completed = counters["completed"]
    n_failed = counters["failed"]
    cancelled = counters["cancelled"]
    final_status = (
        "cancelled" if cancelled
        else ("failed" if n_failed and not n_completed else "done")
    )

    # === book_harmonize (cross-chapter coherence pass, 2026-05-24) =========
    # After all chapters complete (or attempted), run a single book-level
    # harmonization pass that detects + patches definition drift, terminology
    # divergence, and cross-chapter contradictions. Skipped if <2 chapters
    # completed (no cross-chapter coherence to validate).
    # See docs/KD-SYNTH-SOTA-2026-05-24.md §3 #3.
    harmonize_stats: dict | None = None
    if (
        not cancelled
        and n_completed >= 2
        and final_status != "failed"
    ):
        try:
            harmonize_stats = await _run_book_harmonize(
                slug=slug,
                study_thread_id=study_thread_id,
                chapter_ids=[
                    cid for cid in chapter_ids
                    # only chapters that produced render-latest.json
                ],
            )
        except Exception as e:
            logger.warning(
                f"[study-orchestrator] {slug}: book_harmonize crashed "
                f"({type(e).__name__}: {e}) — proceeding without it"
            )
            harmonize_stats = {"skipped": f"crash: {type(e).__name__}"}

    await emit_progress(
        study_thread_id, "study", "study_done",
        n_completed=n_completed,
        n_failed=n_failed,
        n_total=n_total,
        final_status=final_status,
        book_harmonize=harmonize_stats,
    )
    # Mirror to a "synth"-step terminal event too so EventSource handlers
    # that key off `step === 'synth' && kind === 'terminal'` close cleanly.
    await emit_progress(
        study_thread_id, "synth", "terminal",
        status=final_status,
        error=None,
        n_completed=n_completed,
        n_failed=n_failed,
        n_total=n_total,
    )
    logger.info(
        f"[study-orchestrator] {slug}: done — "
        f"{n_completed}/{n_total} completed, {n_failed} failed, "
        f"final_status={final_status}"
    )


# =============================================================================
# Start synth — two modes:
#   - POST /synth/{slug}                    → STUDY mode: orchestrator runs
#                                              ALL chapters sequentially
#   - POST /synth/{slug}?chapter_id=X       → SINGLE mode: just chapter X
# =============================================================================
@router.post("/{slug}")
async def start_synth(
    slug: str,
    chapter_id: str | None = Query(default=None),
    mode: str = Query(default="quality"),
    thread_id: str | None = Query(default=None),
) -> dict:
    """Kick off a synth run.

    Behavior depends on whether `chapter_id` is provided:

      - With `chapter_id`: single-chapter run (escape hatch for re-running
        one specific chapter; preserved for the existing UI single-chapter
        flow). Returns `{thread_id, chapter_id, status: "running"}`.

      - Without `chapter_id`: STUDY mode — spawns the orchestrator
        background task that runs ALL chapters in `plan-latest.json`
        sequentially (sem=1 for v1; raise to 2 if free-tier rotator
        can handle the burst). Returns `{study_thread_id, n_chapters,
        chapter_ids, status: "running"}`. The UI subscribes to the
        study thread for orchestrator events and opens per-chapter
        SSE connections on `chapter_running` events.
    """
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    plan = await _load_plan(slug)
    plan_chapter_ids: list[str] = sorted(
        c["id"] for c in (plan.get("chapters") or [])
        if (c or {}).get("id")
    )
    if not plan_chapter_ids:
        raise HTTPException(
            status_code=404,
            detail=f"plan for {slug!r} has no chapters",
        )

    # ── STUDY MODE — orchestrator over all chapters ────────────────────
    if chapter_id is None:
        study_thread_id = thread_id or _make_study_thread_id(slug)

        # Clear stale cancel flag on the study thread
        r = redis_aio.from_url(
            _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            await clear_cancel(r, study_thread_id)
        finally:
            await r.aclose()

        bg_task = asyncio.create_task(_run_study_orchestrator(
            slug=slug,
            study_thread_id=study_thread_id,
            chapter_ids=plan_chapter_ids,
            mode=mode,
        ))
        _active_runs.add(bg_task)
        bg_task.add_done_callback(_active_runs.discard)

        return {
            "study_thread_id": study_thread_id,
            "slug":            slug,
            "n_chapters":      len(plan_chapter_ids),
            "chapter_ids":     plan_chapter_ids,
            "mode":            mode,
            "concurrency":     _STUDY_SEM,
            "status":          "running",
            "latency_ms":      0,
        }

    # ── SINGLE-CHAPTER MODE — preserved escape hatch ───────────────────
    if chapter_id not in set(plan_chapter_ids):
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} not in plan; known ids: "
                f"{plan_chapter_ids}"
            ),
        )

    if not thread_id:
        thread_id = _make_thread_id(slug, chapter_id)

    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Clear any stale cancel flag
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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
    config = {"configurable": {"thread_id": thread_id}}

    main_task = asyncio.create_task(graph.ainvoke(initial_state, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    bg_task = asyncio.create_task(
        _run_synth_background(
            graph, config, main_task, watcher_task, thread_id,
        )
    )
    _active_runs.add(bg_task)
    bg_task.add_done_callback(_active_runs.discard)

    return {
        "thread_id":  thread_id,
        "slug":       slug,
        "chapter_id": chapter_id,
        "mode":       mode,
        "status":     "running",
        "latency_ms": 0,
    }


# =============================================================================
# Resume
# =============================================================================
def _missing_implemented_nodes(state: dict) -> list[str]:
    missing: list[str] = []
    for name in IMPLEMENTED:
        field = NODE_TO_FIELD.get(name)
        if not field:
            continue
        val = state.get(field)
        if val is None or val == "" or val == []:
            missing.append(name)
    return missing


async def _run_missing_nodes_directly(
    graph, config: dict, thread_id: str, missing: list[str],
) -> None:
    """Catch-up: run nodes that were added to IMPLEMENTED after the
    thread completed (LangGraph would no-op ainvoke(None) on those)."""
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
                f"[synth] {thread_id}: catch-up ran missing node {name!r} "
                f"→ fields {sorted(result.keys())}"
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
        status=terminal_patch.get("status", "unknown"),
        error=terminal_patch.get("error"),
    )


@router.post("/{thread_id:path}/resume")
async def resume_synth(thread_id: str) -> dict:
    """Resume a synth run from its last checkpoint.

    Three paths (mirror planner.resume_planner):
      1. status in {running, failed} → standard ainvoke(None) resume
      2. status == done BUT new IMPLEMENTED nodes haven't run → catch-up
      3. status == done AND no missing nodes → no-op
    """
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}
    snap = await graph.aget_state(config)
    if snap.values == {}:
        raise HTTPException(
            status_code=404,
            detail=(
                f"no checkpoints for thread_id={thread_id!r}; call POST "
                f"/synth/{{slug}} to start a fresh run"
            ),
        )

    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    state = dict(snap.values or {})
    if state.get("status") == "done":
        missing = _missing_implemented_nodes(state)
        if missing:
            await emit_progress(thread_id, "synth", "catch_up", missing=missing)
            try:
                await graph.aupdate_state(config, {"status": "running"})
            except Exception as e:
                logger.warning(
                    f"[synth] {thread_id}: pre-catch-up status reset "
                    f"failed: {type(e).__name__}: {e}"
                )
            bg_task = asyncio.create_task(
                _run_missing_nodes_directly(graph, config, thread_id, missing)
            )
            _active_runs.add(bg_task)
            bg_task.add_done_callback(_active_runs.discard)
            return {
                "thread_id":     thread_id,
                "status":        "catching_up",
                "missing_nodes": missing,
            }
        return {
            "thread_id": thread_id,
            "status":    "done",
            "note":      "all IMPLEMENTED nodes already have output",
        }

    await emit_progress(
        thread_id, "synth", "resumed",
        next_nodes=list(snap.next or []),
    )
    main_task = asyncio.create_task(graph.ainvoke(None, config))
    watcher_task = asyncio.create_task(cancel_watcher(thread_id, main_task))
    bg_task = asyncio.create_task(
        _run_synth_background(graph, config, main_task, watcher_task, thread_id)
    )
    _active_runs.add(bg_task)
    bg_task.add_done_callback(_active_runs.discard)

    return {
        "thread_id":  thread_id,
        "status":     "resuming",
        "next_nodes": list(snap.next or []),
    }


# =============================================================================
# Cancel
# =============================================================================
@router.post("/{thread_id:path}/cancel")
async def cancel_synth(thread_id: str) -> dict:
    """Set the cancel flag — the watcher polling alongside the synth task
    picks it up within ~1s and cancels the main task.

    BUG FIX 2026-05-24: when `thread_id` is a STUDY thread
    (`docs-distiller/study/{slug}/{uuid}`), the study-level flag alone
    is not enough — in-flight per-chapter tasks have their own watchers
    polling their own per-chapter flags. Without propagation, cancelling
    a study only blocks the NEXT chapter from starting; the in-flight
    chapter continues to completion (LLM calls keep firing, the user
    sees the button stuck on "Cancelling…").

    Fix: when path indicates a study thread, scan Redis for active synth
    chapter threads with the matching slug and propagate the cancel flag
    to each. The chapter watchers pick up within ~1s and cancel cleanly.
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    propagated_to: list[str] = []
    try:
        await request_cancel(r, thread_id)
        # Detect study thread + propagate to all active chapter threads
        # for the same slug. Pattern is `docs-distiller/study/{slug}/{uuid}`.
        parts = thread_id.split("/")
        if len(parts) >= 4 and parts[1] == "study":
            slug = parts[2]
            chapter_prefix = f"docs-distiller/synth/{slug}/"
            # Active chapter threads are those with a recent events
            # snapshot key (only created when emit_progress fires) AND
            # without a terminal event yet. The scan is bounded by the
            # ~hundreds-of-keys typical fastmcp/langchain study, so it's
            # cheap; for larger corpora replace with an explicit per-
            # study active-chapter set written by the orchestrator.
            scan_pattern = (
                f"dd:synth:{chapter_prefix}*:events:snapshot"
            )
            try:
                async for key in r.scan_iter(match=scan_pattern, count=200):
                    if isinstance(key, bytes):
                        key = key.decode()
                    # Extract per-chapter thread_id from the key shape:
                    # `dd:synth:{thread_id}:events:snapshot`
                    ch_tid = key[len("dd:synth:"):-len(":events:snapshot")]
                    await request_cancel(r, ch_tid)
                    propagated_to.append(ch_tid)
            except Exception as e:
                logger.warning(
                    f"[cancel_synth] scan/propagate failed for {thread_id!r}: "
                    f"{type(e).__name__}: {e}"
                )
    finally:
        await r.aclose()

    await emit_progress(thread_id, "synth", "cancel_requested")
    # Also emit on every propagated chapter channel so the UI SSE
    # listener can react immediately.
    for ch_tid in propagated_to:
        try:
            await emit_progress(ch_tid, "synth", "cancel_requested")
        except Exception:
            pass
    logger.info(
        f"[cancel_synth] {thread_id}: flag set; "
        f"propagated to {len(propagated_to)} chapter thread(s)"
    )
    return {
        "thread_id":     thread_id,
        "status":        "cancel_requested",
        "propagated_to": propagated_to,
    }


# =============================================================================
# SSE events
# =============================================================================
@router.get("/{thread_id:path}/events")
async def synth_events(thread_id: str) -> StreamingResponse:
    """SSE stream of substep progress events for `thread_id`.

    Mirrors the planner's events endpoint EXACTLY — that's deliberate.
    Two subtleties make real-time delivery work behind nginx-class
    proxies:

      1. Initial comment-line (`: stream open`) flushed BEFORE any
         Redis event arrives — forces the proxy to send headers
         downstream + stop buffering. Without this, the proxy holds
         the response until it accumulates enough bytes, which can
         delay the first event by tens of seconds (or until the
         stream closes — appearing as "no real-time updates").
      2. `X-Accel-Buffering: no` + `Cache-Control: no-cache, no-transform`
         + `Connection: keep-alive` — the canonical SSE-friendly
         header trio that prevents per-byte buffering downstream.

    The stream stays open until the client closes the EventSource.
    The server does NOT terminate on `kind=='terminal'` — the JS
    closes its end after handling the terminal event so it can flush
    the very last paint."""

    async def _gen():
        yield b": stream open\n\n"
        try:
            async for event in subscribe_progress(thread_id):
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache, no-transform",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# =============================================================================
# Debug
# =============================================================================
@router.get("/debug/graph/{thread_id:path}/state")
async def synth_state(thread_id: str) -> dict:
    """Latest LangGraph checkpoint values for the thread."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    snap = await graph.aget_state(config)
    if snap.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints for thread_id={thread_id!r}",
        )
    return {
        "thread_id":   thread_id,
        "values":      dict(snap.values or {}),
        "next":        list(snap.next or []),
        "config":      snap.config,
        "metadata":    snap.metadata,
        "created_at":  str(snap.created_at) if snap.created_at else None,
    }


@router.get("/debug/graph/{thread_id:path}/history")
async def synth_history(thread_id: str) -> dict:
    """All checkpoints (super-steps) for the thread, newest first."""
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    config = {"configurable": {"thread_id": thread_id}}
    history: list[dict] = []
    async for snap in graph.aget_state_history(config):
        history.append({
            "checkpoint_id": (snap.config or {})
                .get("configurable", {})
                .get("checkpoint_id"),
            "next":          list(snap.next or []),
            "metadata":      snap.metadata,
            "created_at":    str(snap.created_at) if snap.created_at else None,
            "state_keys":    sorted((snap.values or {}).keys()),
        })
    return {"thread_id": thread_id, "history": history}


# =============================================================================
# Wipe
# =============================================================================
@router.delete("/{slug}/wipe")
async def wipe_synth(slug: str) -> dict:
    """Delete ALL synth state for `slug`: MinIO synth/{slug}/ blobs +
    Postgres LangGraph checkpoints for any thread under
    docs-distiller/synth/{slug}/."""
    import psycopg

    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = get_storage()
    try:
        n_minio = await minio.delete_prefix(f"synth/{slug}/")
    except Exception as e:
        logger.warning(f"[synth-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    pw = quote(os.environ.get("POSTGRES_PASSWORD", ""), safe="")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get(
        "POSTGRES_DATABASE", os.environ.get("POSTGRES_DB", "postgres"),
    )
    user = os.environ.get("POSTGRES_USER", "postgres")
    dsn = f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    pattern = f"docs-distiller/synth/{slug}/%"
    counts: dict = {}
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True,
        ) as conn:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                async with conn.cursor() as cur:
                    try:
                        await cur.execute(
                            f"DELETE FROM {tbl} WHERE thread_id LIKE %s",
                            (pattern,),
                        )
                        counts[tbl] = cur.rowcount
                    except Exception as e:
                        counts[tbl] = f"skipped: {type(e).__name__}: {e}"
    except Exception as e:
        logger.warning(f"[synth-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    logger.info(
        f"[synth-wipe] {slug}: minio={n_minio} blobs, postgres={counts}"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
    }
