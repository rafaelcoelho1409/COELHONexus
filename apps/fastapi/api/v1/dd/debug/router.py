"""Dev-time stage isolation. Synchronous (no Celery, no lock, no
progress writes) so exceptions surface with full stack traces.
Not gated — single-user dev cluster only."""
import time
from typing import Optional

import domains
import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

from .. import resolver
from . import params

router = APIRouter()




@router.post("/resolve/{slug}")
async def debug_resolve(
    slug: str, 
    entry: resolver.schemas.CatalogEntry) -> dict:
    best = None
    available: list[dict] = []
    for kind in ("llms_full", "llms_txt", "sitemap", "docs", "github"):
        if entry.get(kind):
            available.append({"kind": kind, "url": entry[kind]})
            if best is None:
                best = {"kind": kind, "url": entry[kind]}
    return {
        "slug": slug,
        "framework_name": entry["name"],
        "category": entry.get("category"),
        "best_source": best,
        "all_sources": available,
    }


@router.post("/ingest/{slug}")
async def debug_ingest_one_tier(
    slug: str,
    entry: resolver.schemas.CatalogEntry,
    tier: int,
    language: Optional[str] = None,
) -> dict:
    """tier ∈ 1..5 (llms_full / llms_txt / sitemap / docs / github).
    Writes to the canonical MinIO path, overwriting same-idx bodies."""
    if tier not in params.KIND_BY_TIER:
        raise HTTPException(
            status_code=400,
            detail=f"tier must be 1..5, got {tier}",
        )
    kind = params.KIND_BY_TIER[tier]
    url = entry.get(kind)
    if not url:
        raise HTTPException(
            status_code=400,
            detail=f"{slug!r} has no {kind!r} URL in sources.yaml",
        )

    debug_run_id = f"debug-tier{tier}-{slug}-{int(time.time())}"
    progress = domains.dd.ingestion.runtime.progress.service.Progress(debug_run_id)
    r = redis_aio.from_url(
        domains.dd.planner.keys.redis_url(), 
        socket_connect_timeout=3.0, 
        socket_timeout=10.0,
    )
    minio = domains.dd.ingestion.storage.service.get_storage()
    store = domains.dd.ingestion.storage.service.Store(
        debug_run_id, 
        slug, 
        r, 
        minio)
    try:
        mod = params.TIER_BY_KIND[kind][1]
        kwargs: dict = {
            "url": url, 
            "framework_slug": slug,
            "progress": progress, 
            "store": store,
        }
        if mod in (
            domains.dd.ingestion.tiers.tier3, 
            domains.dd.ingestion.tiers.tier4):
            kwargs["framework_name"] = entry["name"]
        if mod is domains.dd.ingestion.tiers.tier4 and language is not None:
            kwargs["language"] = language
        try:
            await mod.service.run(**kwargs)
        except domains.dd.ingestion.tiers.errors.ManifestDetected as e:
            return {
                "status": "manifest_detected",
                "slug": slug, "tier": kind,
                "debug_run_id": debug_run_id,
                "hint": "use tier=2 next; the body looks like a llms.txt index",
                "details": str(e),
            }
        return {
            "status": "done",
            "slug": slug, "tier": kind, "url": url,
            "debug_run_id": debug_run_id,
            "pages_written": len(store.manifest),
            "total_bytes": sum(e.bytes for e in store.manifest),
        }
    finally:
        try:
            await progress.close()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass


@router.post("/post/{slug}")
async def debug_post(
    slug: str, 
    entry: resolver.schemas.CatalogEntry) -> dict:
    """Re-runs post-process against current MinIO content (useful when
    tuning SPLIT_MIN_SECTION_BYTES without re-downloading)."""
    debug_run_id = f"debug-post-{slug}-{int(time.time())}"
    progress = domains.dd.ingestion.runtime.progress.service.Progress(debug_run_id)
    r = redis_aio.from_url(
        domains.dd.planner.keys.redis_url(), 
        socket_connect_timeout=3.0, 
        socket_timeout=10.0,
    )
    minio = domains.dd.ingestion.storage.service.get_storage()
    try:
        store = await domains.dd.ingestion.storage.service.Store.from_existing(
            debug_run_id, 
            slug, 
            r, 
            minio,
        )
        if not store.manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no MinIO manifest for {slug!r} — run ingest first",
            )
        before = len(store.manifest)
        summary = await domains.dd.ingestion.post.service.apply_to_store(store)
        await progress.record_post(
            input_files=summary["input_files"],
            input_bytes=summary["input_bytes"],
            output_files=summary["output_files"],
            output_bytes=summary["output_bytes"],
            was_split=summary["was_split"],
            stubs_dropped=summary["stubs_dropped"],
            duplicates_dropped=summary["duplicates_dropped"],
            notes="debug-post",
        )
        return {
            "slug": slug,
            "debug_run_id": debug_run_id,
            "before_pages": before,
            "after_pages": len(store.manifest),
            "summary": summary,
        }
    finally:
        try:
            await progress.close()
        except Exception:
            pass
        try:
            await r.aclose()
        except Exception:
            pass


@router.post("/finalize/{slug}")
async def debug_finalize(
    slug: str, 
    entry: resolver.schemas.CatalogEntry) -> dict:
    """Re-writes the canonical manifest from prior in-memory state.
    Useful when the manifest payload shape changes."""
    debug_run_id = f"debug-finalize-{slug}-{int(time.time())}"
    r = redis_aio.from_url(
        domains.dd.planner.keys.redis_url(), 
        socket_connect_timeout=3.0, 
        socket_timeout=10.0,
    )
    minio = domains.dd.ingestion.storage.service.get_storage()
    try:
        store = await domains.dd.ingestion.storage.service.Store.from_existing(
            debug_run_id, 
            slug, 
            r, 
            minio)
        if not store.manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no MinIO manifest for {slug!r} — run ingest first",
            )
        await store.finalize(
            extra={
                "framework_name": entry["name"],
                "run_id": debug_run_id,
                "note": "rewritten by debug-finalize",
        })
        return {
            "slug": slug,
            "debug_run_id": debug_run_id,
            "pages": len(store.manifest),
            "total_bytes": sum(e.bytes for e in store.manifest),
        }
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


@router.post("/snapshot/{slug}")
async def debug_take_snapshot(
    slug: str,
    entry: resolver.schemas.CatalogEntry, 
    label: Optional[str] = None,
) -> dict:
    return await domains.dd.ingestion.storage.service.take_snapshot(
        domains.dd.ingestion.storage.service.get_storage(), 
        slug, 
        label=label)


@router.get("/snapshots/{slug}")
async def debug_list_snapshots(
    slug: str, 
    entry: resolver.schemas.CatalogEntry) -> dict:
    return {
        "slug": slug,
        "snapshots": await domains.dd.ingestion.storage.service.list_snapshots(
            domains.dd.ingestion.storage.service.get_storage(), 
            slug),
    }


@router.post("/restore/{slug}")
async def debug_restore_snapshot(
    slug: str, 
    entry: resolver.schemas.CatalogEntry, 
    ts: str) -> dict:
    try:
        return await domains.dd.ingestion.storage.service.restore_snapshot(
            domains.dd.ingestion.storage.service.get_storage(), 
            slug, 
            ts)
    except ValueError as e:
        raise HTTPException(
            status_code=404, 
            detail=str(e))


@router.delete("/snapshot/{slug}")
async def debug_delete_snapshot(
    slug: str, 
    entry: resolver.schemas.CatalogEntry, 
    ts: str) -> dict:
    return await domains.dd.ingestion.storage.service.delete_snapshot(
        domains.dd.ingestion.storage.service.get_storage(), 
        slug, 
        ts)
