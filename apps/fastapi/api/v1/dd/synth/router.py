"""Synth pipeline endpoints. Per-chapter LangGraph runs on Celery
(queue `synth-{env}`). thread_id = `docs-distiller/synth/{slug}/{uuid}`;
chapter_id lives in SynthState, not the thread_id."""
from __future__ import annotations

from .params import SYNTH_LOCK_TTL_S, VALID_ARTIFACTS, VALID_MODES

import asyncio
import json
import logging
import time

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException, Query, Response
from starlette.responses import StreamingResponse

from domains.dd.ingestion.storage import get_storage
from domains.llm.rotator.discovery import missing_required_keys
from domains.dd.synth.runtime.cancel import clear_cancel, request_cancel
from domains.dd.synth.graph import IMPLEMENTED, NODE_ORDER, build_graph
from domains.dd.synth.keys import (
    active_study_key,
    lock_key,
    redis_url,
    study_timing_key,
)
from domains.dd.synth.params import STUDY_SEM
from domains.dd.synth.runtime.progress import emit_progress, subscribe_progress
from domains.dd.planner.keys import postgres_url
from domains.dd.synth.runtime.dispatch import make_study_thread_id, make_thread_id
from domains.dd.synth.task import (
    resume_synth as resume_synth_task,
    run_single_chapter as run_single_chapter_task,
    run_study as run_study_task,
)

from ..dependencies import get_plan


logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/info")
async def synth_info() -> dict:
    return {
        "node_order":  list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        "modes": [
            {"key": "quality", "label": "Quality (default)", "enabled": True},
            {"key": "fast",    "label": "Fast (3 iters)",    "enabled": False},
        ],
        "status": "live" if IMPLEMENTED else "scaffolding",
    }


@router.get("/{slug}/study/chapters")
async def list_study_chapters(slug: str, response: Response) -> dict:
    """Drives the Study sidebar. `rendered` flags MUST NOT be cached —
    a stale 200 after a wipe would show phantom synthesized chapters."""
    response.headers["Cache-Control"] = "no-store"
    plan = await get_plan(slug)
    chapters_in: list[dict] = plan.get("chapters") or []
    if not chapters_in:
        return {"framework_slug": slug, "chapters": []}

    minio = get_storage()

    # Persisted timing roll-up (per-chapter wall + study total) so the
    # sidebar + navbar show times after a refresh / for cached studies.
    per_chapter_ms: dict = {}
    study_total_wall_ms = 0
    try:
        _t = json.loads(
            await minio.read_text(study_timing_key(slug))
        )
        per_chapter_ms = _t.get("per_chapter_ms") or {}
        study_total_wall_ms = int(_t.get("total_wall_ms") or 0)
    except Exception:
        pass

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
            "wall_ms":    int(per_chapter_ms.get(cid, 0) or 0),
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
                entry["thread_id"] = rp.get("thread_id") or None
            except Exception:
                pass
        out.append(entry)
    return {
        "framework_slug": slug,
        "chapters": out,
        "study_total_wall_ms": study_total_wall_ms,
    }


@router.get("/{slug}/active")
async def synth_active(slug: str, response: Response) -> dict:
    """Page-refresh recovery: returns the study orchestrator's thread_id
    so the UI can reconnect to its SSE without browser localStorage."""
    response.headers["Cache-Control"] = "no-store"
    try:
        r = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            sid = await r.get(active_study_key(slug))
        finally:
            await r.aclose()
    except Exception:
        return {"active": False}
    if not sid:
        return {"active": False}
    if isinstance(sid, (bytes, bytearray)):
        sid = sid.decode("utf-8", "replace")
    try:
        data = json.loads(sid)
        return {
            "active": True,
            "study_thread_id": data.get("study_thread_id"),
            "started_ts": data.get("started_ts"),
        }
    except Exception:
        return {"active": True, "study_thread_id": str(sid), "started_ts": None}


@router.get("/{slug}/study/{chapter_id}/artifact/{artifact_name}")
async def get_study_artifact(
    slug: str, chapter_id: str, artifact_name: str,
) -> StreamingResponse:
    """VALID_ARTIFACTS allow-list prevents arbitrary MinIO key reads."""
    if artifact_name not in VALID_ARTIFACTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid artifact name {artifact_name!r}; valid: "
                f"{sorted(VALID_ARTIFACTS)}"
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
        media_type=VALID_ARTIFACTS[artifact_name],
        headers={
            "Cache-Control": "public, max-age=60",
        },
    )


@router.get("/recent")
async def list_recent_synth() -> dict:
    """Most-recent thread per slug for page-refresh recovery."""
    import psycopg

    dsn = postgres_url()

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


@router.post("/{slug}")
async def start_synth(
    slug: str,
    chapter_id: str | None = Query(default=None),
    mode: str = Query(default="quality"),
    thread_id: str | None = Query(default=None),
) -> dict:
    """No chapter_id → STUDY mode (orchestrator runs all chapters);
    with chapter_id → single-chapter escape hatch."""
    if mode not in VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(VALID_MODES)}",
        )

    _missing = missing_required_keys()
    if _missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "NVIDIA NIM API key required — it powers the mandatory embedding "
                "+ reranking models this run needs. Add "
                + ", ".join(m["key_env"] for m in _missing)
                + " in Settings (/settings), then retry."
            ),
        )

    plan = await get_plan(slug)
    plan_chapter_ids: list[str] = sorted(
        c["id"] for c in (plan.get("chapters") or [])
        if (c or {}).get("id")
    )
    if not plan_chapter_ids:
        raise HTTPException(
            status_code=404,
            detail=f"plan for {slug!r} has no chapters",
        )

    if chapter_id is None:
        study_thread_id = thread_id or make_study_thread_id(slug)

        r = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match="dd:planner:lock:*", count=100,
                )
                for k in keys:
                    ks = k.decode() if isinstance(k, bytes) else k
                    planner_slug = ks.split("dd:planner:lock:", 1)[-1]
                    val = await r.get(ks)
                    if val is None:
                        continue
                    planner_thread = (
                        val.decode() if isinstance(val, bytes) else val
                    )
                    return {
                        "status": "locked",
                        "slug": planner_slug,
                        "thread_id": planner_thread,
                        "stage": "planner",
                        "message": (
                            f"A planner is running ({planner_slug!r}, "
                            f"thread_id={planner_thread}). Planner and "
                            f"Synth share the same LLM resources — "
                            f"running both at once degrades quality on "
                            f"each. Wait for the planner to finish or "
                            f"cancel it before starting a synth."
                        ),
                    }
                if cursor == 0:
                    break

            cursor = 0
            while True:
                cursor, keys = await r.scan(
                    cursor=cursor, match="dd:synth:lock:*", count=100,
                )
                for k in keys:
                    ks = k.decode() if isinstance(k, bytes) else k
                    other_slug = ks.split("dd:synth:lock:", 1)[-1]
                    if other_slug == slug:
                        continue
                    val = await r.get(ks)
                    if val is None:
                        continue
                    other_thread = (
                        val.decode() if isinstance(val, bytes) else val
                    )
                    return {
                        "status": "locked",
                        "slug": other_slug,
                        "thread_id": other_thread,
                        "stage": "synth",
                        "message": (
                            f"Another synth is running ({other_slug!r}, "
                            f"thread_id={other_thread}). Wait for it to "
                            f"finish or cancel it before starting {slug!r}."
                        ),
                    }
                if cursor == 0:
                    break

            acquired = await r.set(
                lock_key(slug), study_thread_id,
                nx=True, ex=SYNTH_LOCK_TTL_S,
            )
            if not acquired:
                existing = await r.get(lock_key(slug))
                existing_tid = (
                    existing.decode() if isinstance(existing, bytes)
                    else existing
                ) if existing else None
                return {
                    "status": "locked",
                    "slug": slug,
                    "thread_id": existing_tid,
                    "stage": "synth",
                    "message": (
                        f"A synth of {slug!r} is already running "
                        f"(thread_id={existing_tid}). Wait for it to "
                        f"finish or cancel it before retrying."
                    ),
                }

            await clear_cancel(r, study_thread_id)

            try:
                async_result = run_study_task.delay(
                    study_thread_id, slug, plan_chapter_ids, mode,
                )
            except Exception as e:
                try:
                    await r.delete(lock_key(slug))
                except Exception:
                    pass
                logger.exception(
                    f"[synth-study] {study_thread_id}: celery dispatch "
                    f"failed: {type(e).__name__}: {e}"
                )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"celery dispatch failed: "
                        f"{type(e).__name__}: {e}"
                    ),
                )
        finally:
            await r.aclose()

        try:
            r2 = redis_aio.from_url(
                redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
            )
            try:
                await r2.set(
                    active_study_key(slug),
                    json.dumps({
                        "study_thread_id": study_thread_id,
                        "started_ts": time.time(),
                    }),
                    ex=14400,
                )
            finally:
                await r2.aclose()
        except Exception as e:
            logger.warning(
                f"[synth-study] {slug}: active-run register failed: "
                f"{type(e).__name__}: {e}"
            )

        return {
            "study_thread_id": study_thread_id,
            "slug":            slug,
            "n_chapters":      len(plan_chapter_ids),
            "chapter_ids":     plan_chapter_ids,
            "mode":            mode,
            "concurrency":     STUDY_SEM,
            "status":          "queued",
            "celery_task_id":  async_result.id,
            "latency_ms":      0,
        }

    if chapter_id not in set(plan_chapter_ids):
        raise HTTPException(
            status_code=404,
            detail=(
                f"chapter {chapter_id!r} not in plan; known ids: "
                f"{plan_chapter_ids}"
            ),
        )

    if not thread_id:
        thread_id = make_thread_id(slug)

    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match="dd:planner:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                planner_slug = ks.split("dd:planner:lock:", 1)[-1]
                val = await r.get(ks)
                if val is None:
                    continue
                planner_thread = (
                    val.decode() if isinstance(val, bytes) else val
                )
                return {
                    "status": "locked",
                    "slug": planner_slug,
                    "thread_id": planner_thread,
                    "stage": "planner",
                    "message": (
                        f"A planner is running ({planner_slug!r}, "
                        f"thread_id={planner_thread}). Planner and "
                        f"Synth share the same LLM resources — running "
                        f"both at once degrades quality on each. Wait "
                        f"for the planner to finish or cancel it before "
                        f"starting a synth."
                    ),
                }
            if cursor == 0:
                break

        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match="dd:synth:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                other_slug = ks.split("dd:synth:lock:", 1)[-1]
                if other_slug == slug:
                    continue
                val = await r.get(ks)
                if val is None:
                    continue
                other_thread = (
                    val.decode() if isinstance(val, bytes) else val
                )
                return {
                    "status": "locked",
                    "slug": other_slug,
                    "thread_id": other_thread,
                    "stage": "synth",
                    "message": (
                        f"Another synth is running ({other_slug!r}, "
                        f"thread_id={other_thread}). Wait for it to "
                        f"finish or cancel it before starting {slug!r}."
                    ),
                }
            if cursor == 0:
                break

        acquired = await r.set(
            lock_key(slug), thread_id,
            nx=True, ex=SYNTH_LOCK_TTL_S,
        )
        if not acquired:
            existing = await r.get(lock_key(slug))
            existing_tid = (
                existing.decode() if isinstance(existing, bytes)
                else existing
            ) if existing else None
            return {
                "status": "locked",
                "slug": slug,
                "thread_id": existing_tid,
                "stage": "synth",
                "message": (
                    f"A synth of {slug!r} is already running "
                    f"(thread_id={existing_tid}). Wait for it to finish "
                    f"or cancel it before retrying."
                ),
            }

        await clear_cancel(r, thread_id)

        try:
            async_result = run_single_chapter_task.delay(
                thread_id, slug, chapter_id, mode,
            )
        except Exception as e:
            try:
                await r.delete(lock_key(slug))
            except Exception:
                pass
            logger.exception(
                f"[synth] {thread_id}: celery dispatch failed: "
                f"{type(e).__name__}: {e}"
            )
            raise HTTPException(
                status_code=503,
                detail=f"celery dispatch failed: {type(e).__name__}: {e}",
            )
    finally:
        await r.aclose()

    return {
        "thread_id":      thread_id,
        "slug":           slug,
        "chapter_id":     chapter_id,
        "mode":           mode,
        "status":         "queued",
        "celery_task_id": async_result.id,
        "latency_ms":     0,
    }


@router.post("/{thread_id:path}/resume")
async def resume_synth(thread_id: str) -> dict:
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    try:
        async_result = resume_synth_task.delay(thread_id)
    except Exception as e:
        logger.exception(
            f"[synth] {thread_id}: celery resume dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    return {
        "thread_id":      thread_id,
        "status":         "queued",
        "celery_task_id": async_result.id,
    }


@router.post("/{thread_id:path}/cancel")
async def cancel_synth(thread_id: str) -> dict:
    """A STUDY thread cancel must propagate to each in-flight per-chapter
    thread — chapter watchers poll their own flag, not the study flag.
    Without propagation only the NEXT chapter is blocked; the in-flight
    one keeps firing LLM calls."""
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    propagated_to: list[str] = []
    try:
        await request_cancel(r, thread_id)
        parts = thread_id.split("/")
        if len(parts) >= 4 and parts[1] == "study":
            slug = parts[2]
            chapter_prefix = f"docs-distiller/synth/{slug}/"
            scan_pattern = (
                f"dd:synth:{chapter_prefix}*:events:snapshot"
            )
            try:
                async for key in r.scan_iter(match=scan_pattern, count=200):
                    if isinstance(key, bytes):
                        key = key.decode()
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


@router.get("/{thread_id:path}/events")
async def synth_events(thread_id: str) -> StreamingResponse:
    """Initial `: stream open` comment forces proxies to flush headers
    (avoid first-event delay). Heartbeat every 15s keeps the connection
    alive through k3d/traefik (default idle-stream timeout 60s) during
    long Synth gaps (sawc_write, book_harmonize)."""
    _SSE_HEARTBEAT_S = 15.0
    _DONE = object()

    async def _gen():
        yield b": stream open\n\n"
        queue: asyncio.Queue = asyncio.Queue()

        async def _pump():
            try:
                async for event in subscribe_progress(thread_id):
                    await queue.put(event)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning(
                    f"[synth-events] {thread_id}: pump crashed "
                    f"({type(e).__name__}: {e})"
                )
            finally:
                await queue.put(_DONE)

        pump_task = asyncio.create_task(_pump())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_S,
                    )
                except asyncio.TimeoutError:
                    yield b": keepalive\n\n"
                    continue
                if event is _DONE:
                    return
                try:
                    payload = json.dumps(event, default=str)
                except Exception:
                    continue
                yield f"data: {payload}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            return
        finally:
            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache, no-transform",
            "Connection":        "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/debug/graph/{thread_id:path}/state")
async def synth_state(thread_id: str) -> dict:
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


@router.delete("/{slug}/wipe")
async def wipe_synth(slug: str) -> dict:
    """Wipes MinIO synth/{slug}/, Postgres checkpoints for synth+study
    threads, Redis SSE snapshots + lock. Without the Redis sweep a wiped
    slug "comes back from the dead" via the cached study SSE snapshot."""
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

    dsn = postgres_url()

    patterns = [
        f"docs-distiller/synth/{slug}/%",
        f"docs-distiller/study/{slug}/%",
    ]
    counts: dict = {}
    try:
        async with await psycopg.AsyncConnection.connect(
            dsn, autocommit=True,
        ) as conn:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                async with conn.cursor() as cur:
                    try:
                        rows = 0
                        for pat in patterns:
                            await cur.execute(
                                f"DELETE FROM {tbl} WHERE thread_id LIKE %s",
                                (pat,),
                            )
                            rows += cur.rowcount
                        counts[tbl] = rows
                    except Exception as e:
                        counts[tbl] = f"skipped: {type(e).__name__}: {e}"
    except Exception as e:
        logger.warning(f"[synth-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    n_redis = 0
    try:
        r = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            for kind in ("synth", "study"):
                match = f"dd:synth:docs-distiller/{kind}/{slug}/*"
                batch: list = []
                async for k in r.scan_iter(match=match, count=500):
                    batch.append(k)
                    if len(batch) >= 500:
                        n_redis += await r.delete(*batch)
                        batch = []
                if batch:
                    n_redis += await r.delete(*batch)
            n_redis += await r.delete(
                active_study_key(slug),
                lock_key(slug),
            )
        finally:
            await r.aclose()
    except Exception as e:
        logger.warning(f"[synth-wipe] Redis delete failed for {slug!r}: {e}")
        n_redis = -1

    logger.info(
        f"[synth-wipe] {slug}: minio={n_minio} blobs, postgres={counts}, "
        f"redis={n_redis} keys"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
        "redis_keys_deleted":    n_redis,
    }
