"""Read-only view of the per-framework MinIO content (canonical post-
finalize corpus). Anything here survives Redis TTL."""

from .params import ARTIFACT_MIME

import logging

import redis.asyncio as redis_aio
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Response

from domains.dd.ingestion.progress import release_lock, read_lock
from domains.dd.ingestion.storage import (
    artifact_key,
    framework_prefix,
    get_storage,
    read_framework_manifest,
    read_framework_page,
)
from domains.dd.resolver import index_by_slug
from domains.dd.planner.keys import redis_url


logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_library() -> list[dict]:
    """Sidebar data source; joins MinIO manifests with resolver catalog
    so each row carries logo URL + framework metadata."""
    minio = get_storage()
    catalog = index_by_slug()
    slugs = await minio.list_subfolders("ingestion/")
    if not slugs:
        return []
    out: list[dict] = []
    for slug in slugs:
        m = await read_framework_manifest(minio, slug)
        if not m:
            continue
        cat = catalog.get(slug, {})
        out.append({
            "slug": slug,
            "framework_name": m.get("framework_name") or cat.get("name") or slug,
            "logo": cat.get("logo"),
            "logos": cat.get("logos") or [],
            "ingested_at": m.get("ingested_at"),
            "page_count": m.get("page_count") or 0,
            "total_bytes": m.get("total_bytes") or 0,
            "tier_kind": m.get("tier_kind"),
            "tier_url": m.get("tier_url"),
            "run_id": m.get("run_id"),
        })
    out.sort(key=lambda e: e.get("ingested_at") or 0, reverse=True)
    return out


@router.get("/{slug}/manifest")
async def get_manifest(slug: str) -> dict:
    m = await read_framework_manifest(get_storage(), slug)
    if not m:
        raise HTTPException(
            status_code=404,
            detail=f"no finalized ingestion for {slug!r}",
        )
    return m


@router.get("/{slug}/pages/{idx}")
async def get_page(slug: str, idx: int) -> dict:
    body = await read_framework_page(get_storage(), slug, idx)
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
    safe_name = (name or "").strip().strip("/").replace("..", "")
    if not safe_name or "/" in safe_name:
        raise HTTPException(status_code=400, detail="invalid artifact name")
    key = artifact_key(slug, safe_name)
    minio = get_storage()
    try:
        data = await minio.read_bytes(key)
    except ClientError as e:
        code = (e.response or {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey"):
            raise HTTPException(
                status_code=404,
                detail=f"artifact {safe_name!r} not found for {slug!r}",
            )
        raise
    ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
    media_type = ARTIFACT_MIME.get(ext, "application/octet-stream")
    return Response(
        content=data, media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Length": str(len(data)),
        },
    )


@router.delete("/{slug}")
async def delete_framework(slug: str) -> dict:
    """Full-wipe every MinIO prefix keyed by this slug + the Redis
    single-flight lock if leaked by a crashed ingestion."""
    minio = get_storage()
    prefixes = (
        framework_prefix(slug),
        f"ingestion-raw/{slug}/",
        f"synth-vault/{slug}/",
        f"planner/{slug}/",
        f"synth/{slug}/",
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

    r = redis_aio.from_url(
        redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
    )
    lock_released = False
    try:
        held_run_id = await read_lock(r, slug)
        if held_run_id:
            lock_released = await release_lock(r, slug, held_run_id)
    except Exception as e:
        logger.warning(
            f"[delete] Redis lock cleanup failed for {slug!r}: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        await r.aclose()

    return {
        "slug":           slug,
        "deleted":        deleted,
        "lock_released":  lock_released,
        "failed_prefixes": failed,
    }
