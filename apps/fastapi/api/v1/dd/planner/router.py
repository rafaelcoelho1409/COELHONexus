"""Planner endpoints. Graph runs in Celery worker `planner-{env}`; FastAPI
is the HTTP/SSE layer. SSE via Redis pub/sub, cancel via Redis flag,
checkpoints in Postgres."""
from __future__ import annotations

from .params import PLANNER_LOCK_TTL_S, VALID_MODES

import asyncio
import json
import logging
import time

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException, Response
from starlette.responses import StreamingResponse

from domains.dd.ingestion.storage import get_storage, read_framework_manifest
from domains.llm.rotator.discovery import missing_required_keys
from domains.dd.planner.cancel import clear_cancel, request_cancel
from domains.dd.planner.graph import IMPLEMENTED, NODE_ORDER, build_graph
from domains.dd.planner.keys import (
    active_run_key,
    lock_key,
    planner_timing_key,
    postgres_url,
    redis_url,
)
from domains.dd.planner.dispatch import make_thread_id
from domains.dd.planner.progress import subscribe_progress
from domains.dd.planner.task import (
    resume_planner as resume_planner_task,
    run_planner as run_planner_task,
)


logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/info")
async def planner_info() -> dict:
    return {
        "node_order": list(NODE_ORDER),
        "implemented": list(IMPLEMENTED),
        "modes": [
            {"key": "llm",       "label": "LLM-only",        "enabled": True},
            {"key": "classical", "label": "Classical + LLM", "enabled": False},
        ],
    }


@router.get("/{slug}/timing")
async def planner_timing(slug: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        raw = await get_storage().read_text(planner_timing_key(slug))
        data = json.loads(raw)
        return {
            "total_wall_ms": int(data.get("total_wall_ms") or 0),
            "finished_ts":   data.get("finished_ts"),
        }
    except Exception:
        return {"total_wall_ms": 0, "finished_ts": None}





@router.post("/{slug}")
async def start_planner(
    slug: str, mode: str = "llm", thread_id: str | None = None,
) -> dict:
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

    if not await read_framework_manifest(get_storage(), slug):
        raise HTTPException(
            status_code=404,
            detail=f"no ingested corpus for {slug!r} — run ingestion first",
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
                cursor=cursor, match="dd:synth:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                synth_slug = ks.split("dd:synth:lock:", 1)[-1]
                val = await r.get(ks)
                if val is None:
                    continue
                synth_thread = (
                    val.decode() if isinstance(val, bytes) else val
                )
                return {
                    "status": "locked",
                    "slug": synth_slug,
                    "thread_id": synth_thread,
                    "stage": "synth",
                    "message": (
                        f"A synth is running ({synth_slug!r}, "
                        f"thread_id={synth_thread}). Planner and Synth "
                        f"share the same LLM resources — running both "
                        f"at once degrades quality on each. Wait for "
                        f"the synth to finish or cancel it before "
                        f"starting a planner."
                    ),
                }
            if cursor == 0:
                break

        cursor = 0
        while True:
            cursor, keys = await r.scan(
                cursor=cursor, match="dd:planner:lock:*", count=100,
            )
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                other_slug = ks.split("dd:planner:lock:", 1)[-1]
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
                    "stage": "planner",
                    "message": (
                        f"Another planner is running ({other_slug!r}, "
                        f"thread_id={other_thread}). Wait for it to "
                        f"finish or cancel it before starting {slug!r}."
                    ),
                }
            if cursor == 0:
                break

        acquired = await r.set(
            lock_key(slug), thread_id,
            nx=True, ex=PLANNER_LOCK_TTL_S,
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
                "stage": "planner",
                "message": (
                    f"A planner of {slug!r} is already running "
                    f"(thread_id={existing_tid}). Wait for it to finish "
                    f"or cancel it before retrying."
                ),
            }

        await clear_cancel(r, thread_id)

        try:
            async_result = run_planner_task.delay(thread_id, slug, mode)
        except Exception as e:
            try:
                await r.delete(lock_key(slug))
            except Exception:
                pass
            logger.exception(
                f"[planner] {thread_id}: celery dispatch failed: "
                f"{type(e).__name__}: {e}"
            )
            raise HTTPException(
                status_code=503,
                detail=f"celery dispatch failed: {type(e).__name__}: {e}",
            )
    finally:
        await r.aclose()

    try:
        r2 = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            await r2.set(
                active_run_key(slug),
                json.dumps({"thread_id": thread_id, "started_ts": time.time()}),
                ex=3600,
            )
        finally:
            await r2.aclose()
    except Exception as e:
        logger.warning(
            f"[planner] {slug}: active-run register failed: "
            f"{type(e).__name__}: {e}"
        )

    return {
        "thread_id":      thread_id,
        "slug":           slug,
        "mode":           mode,
        "status":         "queued",
        "celery_task_id": async_result.id,
        "latency_ms":     0,
    }


@router.get("/{slug}/active")
async def planner_active(slug: str, response: Response) -> dict:
    """Page-refresh recovery without browser localStorage."""
    response.headers["Cache-Control"] = "no-store"
    try:
        r = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            raw = await r.get(active_run_key(slug))
        finally:
            await r.aclose()
    except Exception:
        return {"active": False}
    if not raw:
        return {"active": False}
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
        return {
            "active": True,
            "thread_id": data.get("thread_id"),
            "started_ts": data.get("started_ts"),
        }
    except Exception:
        return {"active": True, "thread_id": str(raw), "started_ts": None}


@router.post("/{thread_id:path}/resume")
async def resume_planner(thread_id: str) -> dict:
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await clear_cancel(r, thread_id)
    finally:
        await r.aclose()

    try:
        async_result = resume_planner_task.delay(thread_id)
    except Exception as e:
        logger.exception(
            f"[planner] {thread_id}: celery resume dispatch failed: "
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


@router.get("/recent")
async def list_recent_planners() -> dict:
    """Per-slug thread selection prefers the thread with the MOST
    checkpoints — sorting by checkpoint_id alone picks abandoned threads
    that died early over later threads that completed all nodes."""
    import psycopg

    dsn = postgres_url()

    out: list[dict] = []
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    WITH thread_stats AS (
                        SELECT
                            split_part(thread_id, '/', 2) AS slug,
                            thread_id,
                            count(*)             AS ckpt_count,
                            max(checkpoint_id)   AS latest_ckpt
                        FROM checkpoints
                        WHERE thread_id LIKE 'docs-distiller/%'
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
        logger.warning(f"[planner-recent] query failed: {e}")
    return {"recent": out}


@router.delete("/{slug}/wipe")
async def wipe_planner(slug: str) -> dict:
    """Wipes MinIO planner/{slug}/, Postgres checkpoints, Redis lock +
    active-run registry. Drops the lock so the next Start Planner click
    isn't rejected by a stale lock that survived the wipe."""
    import psycopg

    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = get_storage()
    try:
        n_minio = await minio.delete_prefix(f"planner/{slug}/")
    except Exception as e:
        logger.warning(f"[planner-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    dsn = postgres_url()

    counts: dict = {}
    pattern = f"docs-distiller/{slug}/%"
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
        logger.warning(f"[planner-wipe] Postgres delete failed for {slug!r}: {e}")
        counts["error"] = f"{type(e).__name__}: {e}"

    n_redis = 0
    try:
        rc = redis_aio.from_url(
            redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
        )
        try:
            n_redis = await rc.delete(
                active_run_key(slug),
                lock_key(slug),
            )
        finally:
            await rc.aclose()
    except Exception as e:
        logger.warning(f"[planner-wipe] Redis delete failed for {slug!r}: {e}")

    logger.info(
        f"[planner-wipe] {slug}: minio={n_minio} blobs, postgres={counts}, "
        f"redis={n_redis}"
    )
    return {
        "slug":                  slug,
        "minio_blobs_deleted":   n_minio,
        "postgres_rows_deleted": counts,
        "redis_keys_deleted":    n_redis,
    }


@router.get("/{thread_id:path}/events")
async def planner_events(thread_id: str) -> StreamingResponse:
    """Initial `: stream open` forces proxies to flush headers (avoids
    first-event delay). 15s heartbeat keeps the connection alive through
    k3d/traefik during long off_topic/cluster gaps."""
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
                    f"[planner-events] {thread_id}: pump crashed "
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


@router.post("/{thread_id:path}/cancel")
async def cancel_planner(thread_id: str) -> dict:
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await request_cancel(r, thread_id)
        if thread_id.count("/") >= 2:
            await r.delete(active_run_key(thread_id.split('/')[1]))
    finally:
        await r.aclose()
    return {"thread_id": thread_id, "status": "cancel_requested"}


@router.get("/debug/graph/{thread_id:path}/state")
async def get_graph_state(thread_id: str) -> dict:
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot.values == {}:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints found for thread_id={thread_id!r}",
        )
    return {
        "thread_id": thread_id,
        "next_nodes": list(snapshot.next or []),
        "values": snapshot.values,
        "config": snapshot.config,
        "metadata": snapshot.metadata,
    }


@router.get("/debug/graph/{thread_id:path}/history")
async def get_graph_history(thread_id: str) -> dict:
    try:
        graph = build_graph()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    config = {"configurable": {"thread_id": thread_id}}
    history = []
    async for snap in graph.aget_state_history(config):
        history.append({
            "checkpoint_id": (snap.config or {}).get(
                "configurable", {},
            ).get("checkpoint_id"),
            "next_nodes": list(snap.next or []),
            "values": snap.values,
            "metadata": snap.metadata,
        })
    if not history:
        raise HTTPException(
            status_code=404,
            detail=f"no checkpoints found for thread_id={thread_id!r}",
        )
    return {"thread_id": thread_id, "count": len(history), "checkpoints": history}
