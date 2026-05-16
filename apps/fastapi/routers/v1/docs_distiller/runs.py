"""Docs Distiller — Runs router (in-flight ingestion lifecycle).

Endpoints:
  POST   /api/v1/docs-distiller/runs          body: {slug, refresh?:bool}
      → 200 {status: "cached", manifest}    if MinIO has a finalized manifest
      → 200 {status: "queued", run_id}      acquired single-flight lock, Celery task dispatched
      → 200 {status: "locked", run_id}      another ingest of this slug is already running

  POST   /api/v1/docs-distiller/runs/{run_id}/cancel
      → sets cancel flag; the running tier picks it up between fetches,
        raises IngestCancelled, dispatcher wipes the partial MinIO
        prefix and releases the lock.

  GET    /api/v1/docs-distiller/runs/{run_id}
      → progress + live manifest + url-records + post-summary (Redis)

  GET    /api/v1/docs-distiller/runs/{run_id}/url-records
  GET    /api/v1/docs-distiller/runs/{run_id}/pages/{idx}
      → per-run convenience reads (also available via the `/ingestion`
        endpoints once the run completes and the manifest moves to MinIO).
"""
import os
import uuid

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.docs_distiller.ingestion.progress import (
    acquire_lock,
    clear_cancel,
    read_lock,
    read_post,
    read_progress,
    read_url_records,
    request_cancel,
)
from services.docs_distiller.ingestion.storage_minio import get_storage
from services.docs_distiller.ingestion.store import (
    read_framework_manifest,
    read_framework_page,
    read_live_manifest,
)

from .resolver import _index_by_slug


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
        if not body.refresh:
            cached = await read_framework_manifest(
                get_storage(), body.slug,
            )
            if cached:
                return {
                    "status": "cached",
                    "slug": body.slug,
                    "run_id": None,
                    "manifest": cached,
                }

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
        from tasks.docs_distiller.ingestion import run_ingestion
        run_ingestion.delay(run_id, body.slug)

        return {
            "status": "queued",
            "slug": body.slug,
            "run_id": run_id,
        }
    finally:
        await r.aclose()


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    """Sets the cancel flag for `run_id`. The running tier picks it up
    between fetches (≤1s latency), raises IngestCancelled, and the
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
    """Convenience: resolve run_id → framework_slug via the live manifest,
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
