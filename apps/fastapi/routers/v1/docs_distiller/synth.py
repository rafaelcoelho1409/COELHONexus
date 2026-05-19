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

from services.docs_distiller.ingestion.storage_minio import get_storage
from services.docs_distiller.synth.cancel import (
    _redis_url,
    clear_cancel,
    request_cancel,
    watcher as cancel_watcher,
)
from services.docs_distiller.synth.graph import (
    IMPLEMENTED,
    NODE_ORDER,
    NODE_REGISTRY,
    NODE_TO_FIELD,
    build_graph,
)
from services.docs_distiller.synth.progress import (
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
    """Canonical synth thread_id format. MUST match
    apps/fasthtml/static/js/docs_distiller.js:_genSynthThreadId so the
    Redis channel + /synth/recent SQL + /synth/{slug}/wipe SQL pattern-
    match correctly across client/server.

    `chapter_id` is intentionally NOT embedded in the thread_id: the JS
    pre-generates a thread_id for the Cancel button BEFORE the POST
    response (no "pending" dead-zone), and at that point it doesn't
    know which chapter the server will pick. The chapter_id lives in
    SynthState instead — recoverable via /debug/graph/{tid}/state."""
    return f"docs-distiller/synth/{slug}/{uuid.uuid4()}"


# =============================================================================
# Start synth (per chapter)
# =============================================================================
@router.post("/{slug}")
async def start_synth(
    slug: str,
    chapter_id: str | None = Query(default=None),
    mode: str = Query(default="quality"),
    thread_id: str | None = Query(default=None),
) -> dict:
    """Kick off a synth run for ONE chapter of `slug`. Detached background
    task; returns immediately with thread_id + chapter_id."""
    if mode not in _VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(_VALID_MODES)}",
        )

    plan = await _load_plan(slug)
    if chapter_id is None:
        chapter_id = _pick_first_chapter_id(plan)
        if not chapter_id:
            raise HTTPException(
                status_code=404,
                detail=f"plan for {slug!r} has no chapters",
            )

    # Validate chapter exists in plan
    chapter_ids = {(c or {}).get("id") for c in (plan.get("chapters") or [])}
    if chapter_id not in chapter_ids:
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} not in plan; known ids: "
                f"{sorted(c for c in chapter_ids if c)}"
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
    picks it up within ~1s and cancels the main task."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await request_cancel(r, thread_id)
    finally:
        await r.aclose()
    await emit_progress(thread_id, "synth", "cancel_requested")
    return {"thread_id": thread_id, "status": "cancel_requested"}


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
