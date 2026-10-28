"""Read-only view of the per-framework MinIO content (canonical post-
finalize corpus). Anything here survives Redis TTL."""
import domains, infra

import logging

from fastapi import APIRouter, HTTPException, Response


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_library() -> list[dict]:
    """Sidebar data source; joins MinIO manifests with resolver catalog
    so each row carries logo URL + framework metadata."""
    minio = infra.minio.service.get_storage()
    catalog = domains.dd.resolver.service.index_by_slug()
    slugs = await minio.list_subfolders("ingestion/")
    if not slugs:
        return []
    out: list[dict] = []
    for slug in slugs:
        m = await domains.dd.ingestion.storage.service.read_framework_manifest(
            minio, 
            slug)
        if not m:
            continue
        out.append(domains.dd.ingestion.storage.domain.library_row(slug, m, catalog.get(slug, {})))
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
    return await domains.dd.ingestion.storage.service.with_display_titles(minio, slug, m)


@router.get("/{slug}/pages/{idx}")
async def get_page(slug: str, idx: int) -> dict:
    body = await domains.dd.ingestion.storage.service.read_framework_page(
        infra.minio.service.get_storage(), 
        slug, 
        idx)
    if body is None:
        raise HTTPException(
            status_code=404,
            detail=f"page idx={idx} not found for {slug!r}",
        )
    return {"slug": slug, "idx": idx, "body": body}


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
        *(prefix for _part, prefix in domains.dd.ingestion.storage.keys.framework_parts(slug)),
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
                r,
                slug)
            if held_run_id:
                lock_released = await domains.dd.ingestion.runtime.progress.service.release_lock(
                    r,
                    slug,
                    held_run_id)
        except Exception as e:
            logger.warning(
                f"[delete] Redis lock cleanup failed for {slug!r}: "
                f"{type(e).__name__}: {e}"
            )
    return {
        "slug":           slug,
        "deleted":        deleted,
        "lock_released":  lock_released,
        "failed_prefixes": failed,
    }
