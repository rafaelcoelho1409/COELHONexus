"""In-flight ingestion lifecycle. Single-flight per slug via Redis lock;
the running tier polls a cancel flag and surrenders cleanly."""
import domains, infra
import domains.dd.ingestion.task
from . import schemas
from .. import resolver

import logging

from fastapi import APIRouter, HTTPException


logger = logging.getLogger(__name__)

router = APIRouter()



@router.post("")
async def start_run(body: schemas.StartRunBody) -> dict:
    """Status: cached (manifest present, no refresh) / queued (lock
    acquired, Celery dispatched) / locked (another in flight)."""
    await resolver.service.get_catalog_entry(body.slug)

    gate = await domains.dd.ingestion.runtime.dispatch.service.reserve_run(body.slug, body.refresh)
    if gate["status"] != "reserved":
        return gate

    run_id = gate["run_id"]
    try:
        task = domains.dd.ingestion.task.run_ingestion.delay(run_id, body.slug)
        # Nice-to-have cross-reference (Tempo/Langfuse UI ↔ task_id in
        # logs) — CeleryInstrumentor already links this HTTP span to the
        # task's own root span via injected trace-context headers, this
        # doesn't replace that.
        infra.opentelemetry.service.tag_celery_task(task.id)
    except Exception:
        await domains.dd.ingestion.runtime.dispatch.service.release_run(body.slug, run_id)
        raise

    return {
        "status": "queued",
        "slug": body.slug,
        "run_id": run_id,
    }


@router.get("/active")
async def list_active_runs() -> dict:
    """Lock-held runs with progress; cross-checked so locks without a
    written progress record (rare race) aren't surfaced."""
    async with infra.redis.service.client() as r:
        active = await domains.dd.ingestion.runtime.progress.service.list_active(r)
    return {"active": active}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    async with infra.redis.service.client() as r:
        await domains.dd.ingestion.runtime.cancel.service.request_cancel(r, run_id)
    return {"run_id": run_id, "status": "cancel_requested"}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    """In-flight snapshot from Redis (canonical post-finalize manifest
    lives in MinIO under /ingestion/{slug})."""
    async with infra.redis.service.client() as r:
        progress = await domains.dd.ingestion.runtime.progress.service.read_progress(r, run_id)
        manifest = await domains.dd.ingestion.storage.service.read_live_manifest(r, run_id)
        post = await domains.dd.ingestion.runtime.progress.service.read_post(r, run_id)

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
    async with infra.redis.service.client() as r:
        return await domains.dd.ingestion.runtime.progress.service.read_url_records(r, run_id)


@router.get("/{run_id}/pages/{idx}")
async def get_page(run_id: str, idx: int) -> dict:
    async with infra.redis.service.client() as r:
        manifest = await domains.dd.ingestion.storage.service.read_live_manifest(r, run_id)
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
        body = await infra.minio.service.get_storage().read_text(key)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"page read failed: {type(e).__name__}: {e}",
        )
    return {"run_id": run_id, "idx": idx, "body": body}
