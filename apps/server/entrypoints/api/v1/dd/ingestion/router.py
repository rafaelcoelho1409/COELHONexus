"""Read-only view of the per-framework MinIO content (canonical post-
finalize corpus). Anything here survives Redis TTL."""

import domains, infra
import domains.dd.ingestion.ai.task

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_library() -> list[dict]:
    """Sidebar data source; joins MinIO manifests with the framework catalog
    so each row carries logo URL + framework metadata."""
    minio = infra.minio.service.get_storage()
    catalog = domains.dd.catalog.service.index_by_slug()
    slugs = domains.dd.ingestion.storage.domain.framework_slugs(
        await minio.list_subfolders(domains.dd.ingestion.storage.keys.library_prefix())
    )
    if not slugs:
        return []
    out: list[dict] = []
    for slug in slugs:
        m = await domains.dd.ingestion.storage.service.read_framework_manifest(
            minio, slug
        )
        if not m:
            continue
        out.append(
            domains.dd.ingestion.storage.domain.library_row(
                slug, m, catalog.get(slug, {})
            )
        )
    return domains.dd.ingestion.storage.domain.newest_ingested_first(out)


@router.get("/{slug}/manifest")
async def get_manifest(slug: str) -> dict:
    minio = infra.minio.service.get_storage()
    m = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
    if not m:
        raise HTTPException(
            status_code=404,
            detail=f"no finalized ingestion for {slug!r}",
        )
    # Pages ingested before titles were derived are named by slug; give them the heading from their body.
    return await domains.dd.ingestion.storage.service.with_display_titles(
        minio, slug, m
    )


@router.get("/{slug}/pages/{idx}")
async def get_page(
    slug: str,
    idx: int,
    version: Literal["processed", "original"] = Query(default="processed"),
) -> dict:
    minio = infra.minio.service.get_storage()
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(
        minio, slug
    )
    entry = domains.dd.ingestion.storage.domain.entry_with_idx(
        (manifest or {}).get("entries", []), idx
    )
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"page idx={idx} not found for {slug!r}",
        )
    key = domains.dd.ingestion.storage.domain.entry_key(slug, entry, idx)
    if version == "original":
        key = entry.get("source_key") or ""
        if not key:
            run_id = (manifest or {}).get("agentic_run_id")
            if isinstance(run_id, str) and run_id:
                candidate_key = domains.dd.ingestion.storage.keys.agentic_manifest_key(
                    slug, run_id
                )
                if await minio.exists(candidate_key):
                    candidate = domains.dd.ingestion.storage.domain.parse_json(
                        await minio.read_text(candidate_key),
                        {},
                    )
                    if isinstance(candidate, dict):
                        page = next(
                            (
                                item
                                for item in candidate.get("pages", [])
                                if isinstance(item, dict) and item.get("idx") == idx
                            ),
                            {},
                        )
                        key = page.get("source_key") or ""
        key = key or entry.get("raw_key") or ""
    if not key:
        raise HTTPException(
            status_code=404, detail=f"page idx={idx} has no {version} version"
        )
    try:
        body = await minio.read_text(key)
    except Exception as error:
        logger.info(
            f"[ingestion] page read failed (idx={idx}, version={version}): {error}"
        )
        raise HTTPException(
            status_code=404,
            detail=f"page idx={idx} {version} version is unavailable for {slug!r}",
        ) from error
    return {"slug": slug, "idx": idx, "version": version, "body": body}


@router.post("/{slug}/agentic/runs", status_code=202)
async def start_agentic_run(slug: str) -> dict:
    """Queue a fresh Agentic AI candidate using saved pages; extraction is not repeated."""
    minio = infra.minio.service.get_storage()
    manifest = await domains.dd.ingestion.storage.service.read_framework_manifest(
        minio, slug
    )
    entries = (manifest or {}).get("entries", [])
    if not isinstance(entries, list) or not entries:
        raise HTTPException(
            status_code=409,
            detail="Complete documentation extraction before starting Agentic AI",
        )
    gate = await domains.dd.ingestion.runtime.dispatch.service.reserve_run(
        slug, refresh=True
    )
    if gate["status"] != "reserved":
        return gate
    run_id = gate["run_id"]
    progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
    try:
        await progress.queue_agentic(len(entries))
        await progress.close()
        task = domains.dd.ingestion.ai.task.run_agentic_candidate.delay(run_id, slug)
        infra.opentelemetry.service.tag_celery_task(task.id)
    except Exception as error:
        await domains.dd.ingestion.runtime.dispatch.service.release_run(slug, run_id)
        progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
        await progress.finish(
            status="failed",
            error=f"Unable to queue Agentic AI: {type(error).__name__}",
        )
        await progress.close()
        logger.exception(f"[dd-agentic] unable to queue run for {slug}")
        raise HTTPException(
            status_code=503, detail="Unable to queue the Agentic AI run"
        ) from error
    return domains.dd.ingestion.runtime.dispatch.domain.queued(slug, run_id)


@router.post("/{slug}/agentic/runs/{run_id}/resume", status_code=202)
async def resume_agentic_run(slug: str, run_id: str) -> dict:
    """Resume a failed or stopped Agentic AI candidate from its saved page inputs and graph checkpoint."""
    try:
        page_count = await domains.dd.ingestion.ai.service.resume_candidate(
            slug, run_id
        )
        progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
        await progress.queue_agentic(page_count)
        await progress.close()
        task = domains.dd.ingestion.ai.task.run_agentic_candidate.delay(run_id, slug)
        infra.opentelemetry.service.tag_celery_task(task.id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        await domains.dd.ingestion.runtime.dispatch.service.release_run(slug, run_id)
        progress = domains.dd.ingestion.runtime.progress.service.Progress(run_id)
        await progress.finish(
            status="failed",
            error=f"Unable to resume Agentic AI: {type(error).__name__}",
        )
        await progress.close()
        logger.exception(f"[dd-agentic] unable to resume run {run_id}")
        raise HTTPException(
            status_code=503, detail="Unable to resume the Agentic AI run"
        ) from error
    return domains.dd.ingestion.runtime.dispatch.domain.queued(slug, run_id)


@router.delete("/{slug}/agentic/runs/{run_id}")
async def wipe_agentic_run(slug: str, run_id: str) -> dict:
    """Wipe one terminal AI candidate and restore its immutable pre-AI pages."""
    try:
        return await domains.dd.ingestion.ai.service.wipe_candidate(slug, run_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get("/{slug}/artifacts/{name}")
async def get_artifact(slug: str, name: str) -> Response:
    """SHA-256-addressed media; bytes are immutable for the life of the
    slug → 1y immutable cache. 404 on miss (no upstream fallback) so the
    broken-image icon makes the absence visible."""
    artifacts = domains.dd.ingestion.artifacts
    safe_name = artifacts.domain.safe_name(name)
    if not artifacts.domain.is_valid_name(safe_name):
        raise HTTPException(status_code=400, detail="invalid artifact name")
    key = domains.dd.ingestion.storage.keys.artifact_key(slug, safe_name)
    minio = infra.minio.service.get_storage()
    try:
        data = await minio.read_bytes(key)
    except infra.minio.service.ClientError as e:
        code = (e.response or {}).get("Error", {}).get("Code", "")
        if code in artifacts.params.MISSING_OBJECT_CODES:
            raise HTTPException(
                status_code=404,
                detail=f"artifact {safe_name!r} not found for {slug!r}",
            )
        raise
    return Response(
        content=data,
        media_type=artifacts.domain.media_type_of(safe_name),
        headers={
            "Cache-Control": artifacts.params.CACHE_CONTROL,
            "Content-Length": str(len(data)),
        },
    )


@router.delete("/{slug}")
async def delete_framework(slug: str) -> dict:
    """Full-wipe every MinIO prefix keyed by this slug + the Redis
    single-flight lock if leaked by a crashed ingestion."""
    minio = infra.minio.service.get_storage()
    prefixes = (
        *(
            prefix
            for _part, prefix in domains.dd.ingestion.storage.keys.framework_parts(slug)
        ),
        domains.dd.planner.keys.minio_prefix(slug),
        domains.dd.synth.keys.minio_prefix(slug),
    )
    deleted = 0
    failed: list[str] = []
    for prefix in prefixes:
        try:
            deleted += await minio.delete_prefix(prefix)
        except Exception as e:
            logger.warning(
                f"[delete] MinIO prefix wipe failed for {prefix!r}: "
                f"{type(e).__name__}: {e}"
            )
            failed.append(prefix)
    lock_released = False
    async with infra.redis.service.client() as r:
        try:
            held_run_id = await domains.dd.ingestion.runtime.progress.service.read_lock(
                r, slug
            )
            if held_run_id:
                lock_released = (
                    await domains.dd.ingestion.runtime.progress.service.release_lock(
                        r, slug, held_run_id
                    )
                )
        except Exception as e:
            logger.warning(
                f"[delete] Redis lock cleanup failed for {slug!r}: "
                f"{type(e).__name__}: {e}"
            )
    return {
        "slug": slug,
        "deleted": deleted,
        "lock_released": lock_released,
        "failed_prefixes": failed,
    }
