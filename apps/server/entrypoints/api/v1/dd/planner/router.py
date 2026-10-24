"""Planner endpoints. Graph runs in Celery worker `planner-{env}`; FastAPI
is the HTTP/SSE layer. SSE via Redis pub/sub, cancel via Redis flag,
checkpoints in Postgres."""
from __future__ import annotations
import domains, infra
import domains.dd.planner.task
from . import params

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Response
from starlette.responses import StreamingResponse


logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/info")
async def planner_info() -> dict:
    return {
        "node_order": list(domains.dd.planner.graph.NODE_ORDER),
        "implemented": list(domains.dd.planner.graph.IMPLEMENTED),
        "modes": [
            {"key": "llm",       "label": "LLM-only",        "enabled": True},
            {"key": "classical", "label": "Classical + LLM", "enabled": False},
        ],
    }


@router.get("/{slug}/health")
async def planner_health(slug: str, response: Response) -> dict:
    """Wave H3 — pipeline health from the latest plan (no MinIO archaeology).
    `degraded` is the one-flag answer to 'will this plan be any good'."""
    response.headers["Cache-Control"] = "no-store"
    try:
        key = domains.dd.planner.nodes.plan_write.keys.latest_blob_key(slug)
        raw = await infra.minio.service.get_storage().read_text(key)
        plan = json.loads(raw)
        stats = plan.get("stats") or {}
        ph = stats.get("pipeline_health") or {}
        return {
            "slug": slug,
            "generated_at": plan.get("generated_at"),
            "n_chapters": stats.get("n_chapters"),
            "n_sources": stats.get("n_sources"),
            "degraded": ph.get("degraded"),
            "pipeline_health": ph,
        }
    except Exception as e:
        return {"slug": slug, "degraded": None, "error": f"{type(e).__name__}: {e}"}


@router.get("/{slug}/timing")
async def planner_timing(slug: str, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        raw = await infra.minio.service.get_storage().read_text(domains.dd.planner.keys.planner_timing_key(slug))
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
    if mode not in params.VALID_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid mode {mode!r}; expected one of {sorted(params.VALID_MODES)}",
        )

    _manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(infra.minio.service.get_storage(), slug)
    if not _manifest:
        raise HTTPException(
            status_code=404,
            detail=f"no ingested corpus for {slug!r} — run ingestion first",
        )

    if not thread_id:
        thread_id = domains.dd.planner.runtime.dispatch.keys.make_thread_id(slug)

    gate = await domains.dd.planner.runtime.dispatch.service.reserve_start(slug, thread_id)
    if gate is not None:
        return gate

    try:
        async_result = domains.dd.planner.task.run_planner.delay(thread_id, slug, mode)
        infra.opentelemetry.service.tag_celery_task(async_result.id)
    except Exception as e:
        await domains.dd.planner.runtime.dispatch.service.release_start(slug)
        logger.exception(
            f"[planner] {thread_id}: celery dispatch failed: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=503,
            detail=f"celery dispatch failed: {type(e).__name__}: {e}",
        )

    await domains.dd.planner.runtime.dispatch.service.register_active_run(slug, thread_id)

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
    return await domains.dd.planner.runtime.dispatch.service.read_active_run(slug)


@router.post("/{thread_id:path}/resume")
async def resume_planner(thread_id: str) -> dict:
    async with infra.redis.service.client() as r:
        await domains.dd.planner.runtime.cancel.service.clear_cancel(r, thread_id)

    try:
        async_result = domains.dd.planner.task.resume_planner.delay(thread_id)
        infra.opentelemetry.service.tag_celery_task(async_result.id)
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
    out: list[dict] = []
    try:
        with infra.postgres.service.span(
            "select", **{"db.postgres.table": "checkpoints"},
        ):
            async with await infra.postgres.service.connect() as conn:
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
                    rows = await cur.fetchall()
        for slug, tid, ckpt_count, latest in rows:
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
    if not slug or "/" in slug:
        raise HTTPException(
            status_code=400,
            detail=f"invalid slug {slug!r}; slashes not allowed",
        )

    minio = infra.minio.service.get_storage()
    try:
        n_minio = await minio.delete_prefix(f"planner/{slug}/")
    except Exception as e:
        logger.warning(f"[planner-wipe] MinIO delete failed for {slug!r}: {e}")
        n_minio = -1

    counts: dict = {}
    pattern = f"docs-distiller/{slug}/%"
    try:
        with infra.postgres.service.span(
            "delete", **{"db.postgres.table": "checkpoints,checkpoint_writes,checkpoint_blobs"},
        ):
            async with await infra.postgres.service.connect(autocommit=True) as conn:
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

    n_redis = await domains.dd.planner.runtime.dispatch.service.wipe_redis(slug)

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


@router.post("/prune")
async def prune_checkpoints(older_than_days: int = 30, dry_run: bool = True) -> dict:
    """Manual trigger for the checkpoint-retention sweep (see
    `runtime.checkpoint.service.prune_old_threads`). `dry_run=true`
    (default) previews eligible thread_ids inline, no Celery hop, no
    deletion. `dry_run=false` dispatches to Celery — a full-table scan
    across every slug can run long, and this actually deletes rows, so
    it shouldn't block the request or run without an explicit opt-in.
    Nothing in this codebase calls this on a schedule; wire it up via a
    k8s CronJob or celery beat if periodic pruning is wanted."""
    if dry_run:
        result = await domains.dd.planner.runtime.checkpoint.service.prune_old_threads(
            older_than_days = older_than_days, dry_run = True,
        )
        return result
    async_result = domains.dd.planner.task.prune_old_checkpoints.delay(
        older_than_days, False,
    )
    return {
        "dispatched":     True,
        "celery_task_id": async_result.id,
        "older_than_days": older_than_days,
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
                async for event in domains.dd.planner.runtime.progress.service.subscribe_progress(thread_id):
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
    await domains.dd.planner.runtime.dispatch.service.cancel_run(thread_id)
    return {"thread_id": thread_id, "status": "cancel_requested"}


@router.get("/debug/graph/{thread_id:path}/state")
async def get_graph_state(thread_id: str) -> dict:
    try:
        graph = domains.dd.planner.graph.build_graph()
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


@router.get("/debug/graph/{thread_id:path}/llm-counters")
async def get_graph_llm_counters(thread_id: str) -> dict:
    return await domains.settings.llm_counter.service.read_counters("dd", thread_id)


@router.get("/debug/graph/{thread_id:path}/history")
async def get_graph_history(thread_id: str) -> dict:
    try:
        graph = domains.dd.planner.graph.build_graph()
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
