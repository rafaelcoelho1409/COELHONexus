"""In-flight ingestion lifecycle. Single-flight per slug via Redis lock;
the running tier polls a cancel flag and surrenders cleanly."""

from .schemas import StartRunBody

import uuid

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

from domains.dd.ingestion.progress import (
    acquire_lock,
    clear_cancel,
    read_lock,
    read_post,
    read_progress,
    read_url_records,
    release_lock,
    request_cancel,
)
from domains.dd.ingestion.storage import (
    framework_prefix,
    get_storage,
    read_framework_manifest,
    read_live_manifest,
)
from domains.dd.planner.keys import redis_url

from ..dependencies import get_catalog_entry


router = APIRouter()



@router.post("")
async def start_run(body: StartRunBody) -> dict:
    """Status: cached (manifest present, no refresh) / queued (lock
    acquired, Celery dispatched) / locked (another in flight)."""
    entry = await get_catalog_entry(body.slug)

    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        cursor = 0
        running_slug = None
        running_run_id = None
        while True:
            cursor, keys = await r.scan(cursor=cursor, match="dd:lock:*", count=100)
            for k in keys:
                ks = k.decode() if isinstance(k, bytes) else k
                other_slug = ks.split("dd:lock:", 1)[-1]
                if other_slug == body.slug:
                    continue
                val = await r.get(ks)
                if val is None:
                    continue
                running_slug = other_slug
                running_run_id = val.decode() if isinstance(val, bytes) else val
                break
            if running_slug is not None or cursor == 0:
                break
        if running_slug is not None:
            return {
                "status": "locked",
                "slug": running_slug,
                "run_id": running_run_id,
                "message": (
                    f"Another ingestion is running ({running_slug!r}, "
                    f"run_id={running_run_id}). Wait for it to finish or "
                    f"cancel it before starting {body.slug!r}."
                ),
            }
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
            import logging
            _log = logging.getLogger(__name__)
            for prefix in (
                framework_prefix(body.slug),
                f"ingestion-raw/{body.slug}/",
                f"synth-vault/{body.slug}/",
            ):
                try:
                    n = await minio.delete_prefix(prefix)
                    if n:
                        _log.info(
                            f"[runs] refresh wipe: deleted {n} stale objects "
                            f"from {prefix!r} before re-ingestion"
                        )
                except Exception as e:
                    _log.warning(
                        f"[runs] refresh wipe failed for {prefix!r}: {e}"
                    )

        run_id = uuid.uuid4().hex
        if not await acquire_lock(r, body.slug, run_id):
            active = await read_lock(r, body.slug)
            return {
                "status": "locked",
                "slug": body.slug,
                "run_id": active or "?",
                "message": "Concurrent acquire race; try again.",
            }

        await clear_cancel(r, run_id)

        try:
            from domains.dd.ingestion.task import run_ingestion
            run_ingestion.delay(run_id, body.slug)
        except Exception:
            try:
                await release_lock(r, body.slug, run_id)
            except Exception:
                pass
            raise

        return {
            "status": "queued",
            "slug": body.slug,
            "run_id": run_id,
        }
    finally:
        await r.aclose()


@router.get("/active")
async def list_active_runs() -> dict:
    """Lock-held runs with progress; cross-checked so locks without a
    written progress record (rare race) aren't surfaced."""
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    active: list[dict] = []
    try:
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
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        await request_cancel(r, run_id)
    finally:
        await r.aclose()
    return {"run_id": run_id, "status": "cancel_requested"}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """In-flight snapshot from Redis (canonical post-finalize manifest
    lives in MinIO under /ingestion/{slug})."""
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    try:
        return await read_url_records(r, run_id)
    finally:
        await r.aclose()


@router.get("/{run_id}/pages/{idx}")
async def get_page(run_id: str, idx: int) -> dict:
    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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
