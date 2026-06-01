"""Docs Distiller — Library router (persistent ingestion artifacts).

Read-only view of the per-framework MinIO content. Anything written here
survives Redis TTL — it's the canonical, deduplicable corpus that future
per-experience-level synth (senior / mid / junior) will reuse without
re-downloading.

  GET    /api/v1/docs-distiller/ingestion
      -> summary list of every framework whose ingestion has been
         finalized (sidebar data source in FastHTML).

  GET    /api/v1/docs-distiller/ingestion/{slug}/manifest
      -> full manifest dict (entries + ingest metadata).

  GET    /api/v1/docs-distiller/ingestion/{slug}/pages/{idx}
      -> raw markdown body for one page.

  DELETE /api/v1/docs-distiller/ingestion/{slug}
      -> full-wipe: ingestion + ingestion-raw + synth-vault + planner +
         synth prefixes in MinIO, plus the dd:lock:{slug} Redis key if
         held. Next POST /runs {slug} starts from scratch.
"""
import logging
import os

import redis.asyncio as redis_aio
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Response

from domains.dd.ingestion.progress import release_lock, read_lock
from domains.dd.ingestion.storage import (
    framework_prefix,
    get_storage,
)
from domains.dd.ingestion.storage import (
    read_framework_manifest,
    read_framework_page,
)
from domains.dd.ingestion.storage.constants import artifact_key

from domains.dd.resolver import _index_by_slug


# MIME types we serve from the artifact endpoint. Derived from the
# filename extension (the artifact filename IS ``{sha256[:16]}.{ext}``),
# so every served byte stream gets the right Content-Type without an
# extra MinIO HEAD call.
_ARTIFACT_MIME: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "avif": "image/avif", "ico": "image/x-icon", "bmp": "image/bmp",
    "tiff": "image/tiff",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "mkv": "video/x-matroska", "ogv": "video/ogg",
    "mp3": "audio/mpeg", "ogg": "audio/ogg", "wav": "audio/wav",
    "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac",
    "weba": "audio/webm",
}


logger = logging.getLogger(__name__)
router = APIRouter()


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
    port = os.environ.get("REDIS_PORT", "6379")
    pwd = os.environ.get("REDIS_PASSWORD", "")
    return f"redis://:{pwd}@{host}:{port}" if pwd else f"redis://{host}:{port}"


@router.get("")
async def list_library() -> list[dict]:
    """Sidebar data source: one entry per finalized framework, sorted by
    most-recently-ingested first. Joins MinIO manifests with the resolver
    catalog so each row carries the logo URL (for the sidebar avatar)."""
    minio = get_storage()
    catalog = _index_by_slug()
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
    """Full manifest for a finalized framework. 404 when no ingestion
    has completed for this slug yet."""
    m = await read_framework_manifest(get_storage(), slug)
    if not m:
        raise HTTPException(
            status_code=404,
            detail=f"no finalized ingestion for {slug!r}",
        )
    return m


@router.get("/{slug}/pages/{idx}")
async def get_page(slug: str, idx: int) -> dict:
    """Raw markdown body for one page of `slug`."""
    body = await read_framework_page(get_storage(), slug, idx)
    if body is None:
        raise HTTPException(
            status_code=404,
            detail=f"page idx={idx} not found for {slug!r}",
        )
    return {"slug": slug, "idx": idx, "body": body}


@router.get("/{slug}/artifacts/{name}")
async def get_artifact(slug: str, name: str) -> Response:
    """Stream an extracted media artifact (image / gif / video / audio)
    from ``ingestion/{slug}/artifacts/{name}``. Content-addressed by
    SHA-256, so served bytes are immutable for the life of the slug —
    1-year ``Cache-Control: immutable`` is safe.

    Markdown rendered in the FastHTML drawer references this endpoint
    directly via the URLs that `domains/dd/ingestion/artifacts.py`
    rewrites at ingest time. No upstream-URL fallback: a missing
    artifact returns 404 so the caller (markdown ``<img>``) renders the
    browser's broken-image icon and the absence is visible.
    """
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
    media_type = _ARTIFACT_MIME.get(ext, "application/octet-stream")
    return Response(
        content=data, media_type=media_type,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Length": str(len(data)),
        },
    )


@router.delete("/{slug}")
async def delete_framework(slug: str) -> dict:
    """Full-wipe a framework: every MinIO prefix that holds data keyed by
    this slug, plus the Redis single-flight lock if held.

    Wiped prefixes:
      ingestion/{slug}/         canonical pages + manifest (the sidebar source)
      ingestion-raw/{slug}/     pre-normalization monolith (reversibility data)
      synth-vault/{slug}/       sentinelized bodies + vault.json (synth inputs)
      planner/{slug}/           planner artifacts (corpus_load → plan-latest.json)
      synth/{slug}/             synth artifacts (outline → render output)

    Plus dd:lock:{slug} in Redis if a previous ingestion crashed and leaked it.

    After this call the slug is brand-new across the entire pipeline — next
    POST /runs {slug} re-ingests from scratch + downstream planner/synth
    start with clean state. `slug` is taken literally; passing a run_id
    instead wipes orphan content from any pre-fix-keyed runs.

    Returns the count of MinIO objects removed across all prefixes.
    Per-prefix failures are logged but don't abort the wipe — best-effort
    cleanup so a partial delete still removes most stale state.
    """
    minio = get_storage()
    prefixes = (
        framework_prefix(slug),       # ingestion/{slug}/
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

    # Clear the single-flight lock if held — a previous crashed ingestion may
    # have leaked it (the 35-min TTL would otherwise block re-ingestion).
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=5.0,
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
