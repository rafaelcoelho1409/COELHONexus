"""Docs Distiller — Runs router (in-flight ingestion lifecycle).

Endpoints:
  POST   /api/v1/docs-distiller/runs          body: {slug, refresh?:bool}
      -> 200 {status: "cached", manifest}    if MinIO has a finalized manifest
      -> 200 {status: "queued", run_id}      acquired single-flight lock, Celery task dispatched
      -> 200 {status: "locked", run_id}      another ingest of this slug is already running

  POST   /api/v1/docs-distiller/runs/{run_id}/cancel
      -> sets cancel flag; the running tier picks it up between fetches,
        raises IngestCancelled, dispatcher wipes the partial MinIO
        prefix and releases the lock.

  GET    /api/v1/docs-distiller/runs/{run_id}
      -> progress + live manifest + url-records + post-summary (Redis)

  GET    /api/v1/docs-distiller/runs/{run_id}/url-records
  GET    /api/v1/docs-distiller/runs/{run_id}/pages/{idx}
      -> per-run convenience reads (also available via the `/ingestion`
        endpoints once the run completes and the manifest moves to MinIO).
"""
import os
import uuid

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from domains.dd.ingestion.progress import (
    acquire_lock,
    clear_cancel,
    read_lock,
    read_post,
    read_progress,
    read_url_records,
    request_cancel,
)
from domains.dd.ingestion.storage import (
    framework_prefix,
    get_storage,
)
from domains.dd.ingestion.storage import (
    read_framework_manifest,
    read_framework_page,
    read_live_manifest,
)

from domains.dd.resolver import _index_by_slug


router = APIRouter()


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
    port = os.environ.get("REDIS_PORT", "6379")
    pwd = os.environ.get("REDIS_PASSWORD", "")
    return f"redis://:{pwd}@{host}:{port}" if pwd else f"redis://{host}:{port}"


class StartRunBody(BaseModel):
    slug: str
    refresh: bool = False


@router.post("")
async def start_run(body: StartRunBody) -> dict:
    """Single-flight ingestion. Returns one of three statuses:

      "cached"  — MinIO already has a finalized manifest for this slug
                  (and the request didn't set refresh=true). No Celery
                  task spawned. UI advances straight to Step 3 with a
                  brief "loaded from cache · ingested X ago" notice.
      "queued"  — lock acquired, Celery task dispatched, returns the
                  new run_id for live progress polling.
      "locked"  — another ingest of this slug is already in flight;
                  returns the active run_id so the UI can attach to it
                  (or show a "denied" warning).
    """
    catalog = _index_by_slug()
    if body.slug not in catalog:
        raise HTTPException(
            status_code=404,
            detail=f"unknown framework slug: {body.slug!r}",
        )

    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        # 1. Single-flight: is something already running for this slug?
        active = await read_lock(r, body.slug)
        if active:
            return {
                "status": "locked",
                "slug": body.slug,
                "run_id": active,
                "message": (
                    f"An ingestion of {body.slug!r} is already running "
                    f"(run_id={active}). Wait for it to finish or cancel "
                    f"it before triggering another."
                ),
            }

        # 2. Cached: skip ingestion unless refresh requested.
        minio = get_storage()
        if not body.refresh:
            cached = await read_framework_manifest(minio, body.slug)
            if cached:
                return {
                    "status": "cached",
                    "slug": body.slug,
                    "run_id": None,
                    "manifest": cached,
                }
        else:
            # Refresh: wipe the framework prefix before queuing so the
            # new ingestion writes against a clean slate. Without this,
            # old pages (especially when the new run produces fewer
            # pages than the previous one — e.g. after a splitter
            # change) stay as orphans under the same prefix and
            # contaminate later reads + `delete_prefix` calls.
            try:
                n = await minio.delete_prefix(framework_prefix(body.slug))
                if n:
                    import logging
                    logging.getLogger(__name__).info(
                        f"[runs] refresh wipe: deleted {n} stale objects "
                        f"for {body.slug!r} before re-ingestion"
                    )
            except Exception as e:
                # Don't block re-ingestion on cleanup failure — the new
                # writes will at least overwrite the colliding keys.
                import logging
                logging.getLogger(__name__).warning(
                    f"[runs] refresh wipe failed for {body.slug!r}: {e}"
                )

        # 3. Acquire lock + queue task.
        run_id = uuid.uuid4().hex
        if not await acquire_lock(r, body.slug, run_id):
            # Race: someone else acquired between our read_lock and acquire.
            active = await read_lock(r, body.slug)
            return {
                "status": "locked",
                "slug": body.slug,
                "run_id": active or "?",
                "message": "Concurrent acquire race; try again.",
            }

        await clear_cancel(r, run_id)

        # Late import — defer the Celery app import past FastAPI startup.
        from ..ingestion.task import run_ingestion
        run_ingestion.delay(run_id, body.slug)

        return {
            "status": "queued",
            "slug": body.slug,
            "run_id": run_id,
        }
    finally:
        await r.aclose()


@router.get("/active")
async def list_active_runs() -> dict:
    """Return every in-flight ingestion (lock-held with a still-running
    progress record). Page-reload recovery: the FastHTML UI calls this on
    init so that closing/reopening a tab mid-ingestion restores the
    progress display + resumes polling — and crucially prevents the user
    from triggering a duplicate run for a slug that's already in flight.

    Source of truth: `dd:lock:*` keys in Redis (set when POST /runs
    acquires the single-flight lock, released in dispatch.py's finally
    block). Cross-checked with the per-run progress record so we don't
    show locks whose progress was never written (rare race window).
    """
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    active: list[dict] = []
    try:
        # SCAN > KEYS for safety on large keyspaces; same semantics here
        # since the lock set is tiny (1 per active framework).
        cursor = 0
        while True:
            cursor, keys = await r.scan(cursor=cursor, match="dd:lock:*", count=100)
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                slug = ks.split("dd:lock:", 1)[-1]
                run_id_raw = await r.get(k)
                if not run_id_raw:
                    continue
                run_id = (
                    run_id_raw.decode()
                    if isinstance(run_id_raw, bytes) else run_id_raw
                )
                progress = await read_progress(r, run_id)
                # Only surface runs that have written progress AND aren't
                # already terminal (in case the lock is being released).
                if progress and progress.get("status") in ("running", "idle"):
                    active.append({
                        "slug": slug,
                        "run_id": run_id,
                        "progress": progress,
                    })
            if cursor == 0:
                break
    finally:
        await r.aclose()
    return {"active": active}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """Sets the cancel flag for `run_id`. The running tier picks it up
    between fetches (<=1s latency), raises IngestCancelled, and the
    dispatcher wipes the partial MinIO prefix + releases the lock."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await request_cancel(r, run_id)
    finally:
        await r.aclose()
    return {"run_id": run_id, "status": "cancel_requested"}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """In-flight snapshot: progress + live manifest + post-process summary.
    Reads exclusively from Redis (canonical post-finalize manifest lives
    in MinIO under /ingestion/{slug})."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        progress = await read_progress(r, run_id)
        manifest = await read_live_manifest(r, run_id)
        post = await read_post(r, run_id)
    finally:
        await r.aclose()

    if progress is None and not manifest:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")

    return {
        "run_id": run_id,
        "progress": progress,
        "manifest": manifest,
        "post": post,
    }


@router.get("/{run_id}/url-records")
async def get_url_records(run_id: str) -> list[dict]:
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        return await read_url_records(r, run_id)
    finally:
        await r.aclose()


@router.get("/{run_id}/pages/{idx}")
async def get_page(run_id: str, idx: int) -> dict:
    """Convenience: resolve run_id -> framework_slug via the live manifest,
    then read the body from MinIO. Persistent-side equivalent lives at
    /api/v1/docs-distiller/ingestion/{slug}/pages/{idx}."""
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        manifest = await read_live_manifest(r, run_id)
    finally:
        await r.aclose()
    if not manifest or idx < 0 or idx >= len(manifest):
        raise HTTPException(
            status_code=404,
            detail=f"page idx={idx} not found in run {run_id!r}",
        )
    entry = manifest[idx]
    key = entry.get("key")
    if not key:
        raise HTTPException(
            status_code=404,
            detail=f"manifest entry has no MinIO key (idx={idx})",
        )
    try:
        body = await get_storage().read_text(key)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"page read failed: {type(e).__name__}: {e}",
        )
    return {"run_id": run_id, "idx": idx, "body": body}
