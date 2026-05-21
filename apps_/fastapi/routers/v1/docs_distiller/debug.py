"""Docs Distiller — Debug router (dev-time stage isolation).

Lets you run each ingestion stage independently without re-executing the
whole pipeline. All endpoints are synchronous (no Celery, no
single-flight lock, no progress writes that affect production state) so a
curl gives you the result immediately and exceptions surface with full
stack traces.

Pair with the snapshot endpoints to freeze state before a tweak, then
restore if the change made things worse.

Endpoints (all under /api/v1/docs-distiller/debug):

  POST /resolve/{slug}                       — returns the resolver's best-source pick
  POST /ingest/{slug}?tier=N                 — runs ONE tier in isolation, writes to canonical MinIO path
  POST /post/{slug}                          — re-runs post-process against current MinIO content
  POST /finalize/{slug}                      — re-writes the canonical manifest from in-memory state

  POST   /snapshot/{slug}?label=…            — freeze current state under _snapshots/{ts}
  GET    /snapshots/{slug}                   — list snapshot timestamps (newest first)
  POST   /restore/{slug}?ts=…                — overwrite canonical with the snapshot
  DELETE /snapshot/{slug}?ts=…               — delete a snapshot

Not gated by env in this MVP — single-user dev cluster. Add a feature flag
before exposing to multi-tenant or prod.
"""
import os
import time
import uuid
from typing import Optional

import redis.asyncio as redis_aio
from fastapi import APIRouter, HTTPException

from services.docs_distiller.ingestion import (
    post,
    snapshot,
    tier1_llms_full,
    tier2_llms_txt,
    tier3_sitemap,
    tier4_http,
    tier5_github,
)
from services.docs_distiller.ingestion.progress import Progress
from services.docs_distiller.ingestion.storage_minio import get_storage
from services.docs_distiller.ingestion.store import Store

from .resolver import _index_by_slug


router = APIRouter()


_TIER_BY_KIND = {
    "llms_full": (1, tier1_llms_full),
    "llms_txt":  (2, tier2_llms_txt),
    "sitemap":   (3, tier3_sitemap),
    "docs":      (4, tier4_http),
    "github":    (5, tier5_github),
}
_KIND_BY_TIER = {n: kind for kind, (n, _) in _TIER_BY_KIND.items()}


def _redis_url() -> str:
    host = os.environ.get("REDIS_HOST", "redis-master.redis.svc.cluster.local")
    port = os.environ.get("REDIS_PORT", "6379")
    pwd = os.environ.get("REDIS_PASSWORD", "")
    return f"redis://:{pwd}@{host}:{port}" if pwd else f"redis://{host}:{port}"


def _entry_or_404(slug: str) -> dict:
    entry = _index_by_slug().get(slug)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown slug: {slug!r}")
    return entry


# =============================================================================
# Stage 1 — Resolve
# =============================================================================
@router.post("/resolve/{slug}")
async def debug_resolve(slug: str) -> dict:
    entry = _entry_or_404(slug)
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


# =============================================================================
# Stage 2 — Single-tier ingest
# =============================================================================
@router.post("/ingest/{slug}")
async def debug_ingest_one_tier(
    slug: str,
    tier: int,
    language: Optional[str] = None,
) -> dict:
    """Run one tier in isolation. `tier` ∈ 1..5 (1=llms_full, 2=llms_txt,
    3=sitemap, 4=docs/HTTP, 5=github). The chosen tier writes pages to the
    canonical per-framework MinIO path, overwriting prior bodies for the
    same indices. Re-running tier 4 after tweaking a filter, for example,
    just calls this endpoint — no Celery, no full pipeline."""
    if tier not in _KIND_BY_TIER:
        raise HTTPException(
            status_code=400,
            detail=f"tier must be 1..5, got {tier}",
        )
    entry = _entry_or_404(slug)
    kind = _KIND_BY_TIER[tier]
    url = entry.get(kind)
    if not url:
        raise HTTPException(
            status_code=400,
            detail=f"{slug!r} has no {kind!r} URL in sources.yaml",
        )

    debug_run_id = f"debug-tier{tier}-{slug}-{int(time.time())}"
    progress = Progress(debug_run_id)
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=10.0,
    )
    minio = get_storage()
    store = Store(debug_run_id, slug, r, minio)

    try:
        mod = _TIER_BY_KIND[kind][1]
        kwargs: dict = {
            "url": url, "framework_slug": slug,
            "progress": progress, "store": store,
        }
        if mod in (tier3_sitemap, tier4_http):
            kwargs["framework_name"] = entry["name"]
        if mod is tier4_http and language is not None:
            kwargs["language"] = language
        try:
            await mod.run(**kwargs)
        except tier1_llms_full.ManifestDetected as e:
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


# =============================================================================
# Stage 3 — Post-process (against the current canonical MinIO state)
# =============================================================================
@router.post("/post/{slug}")
async def debug_post(slug: str) -> dict:
    """Re-run `post.apply_to_store` against whatever's currently in MinIO
    for this framework. Useful when tuning SPLIT_MIN_SECTION_BYTES or the
    monolith threshold without re-downloading."""
    _entry_or_404(slug)
    debug_run_id = f"debug-post-{slug}-{int(time.time())}"
    progress = Progress(debug_run_id)
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=10.0,
    )
    minio = get_storage()

    try:
        store = await Store.from_existing(
            debug_run_id, slug, r, minio,
        )
        if not store.manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no MinIO manifest for {slug!r} — run ingest first",
            )
        before = len(store.manifest)
        summary = await post.apply_to_store(store)
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


# =============================================================================
# Stage 4 — Finalize (re-write the canonical manifest)
# =============================================================================
@router.post("/finalize/{slug}")
async def debug_finalize(slug: str) -> dict:
    """Re-write the canonical MinIO manifest from the existing in-memory
    state (loaded from the prior manifest). Useful when the manifest
    payload shape changes."""
    entry = _entry_or_404(slug)
    debug_run_id = f"debug-finalize-{slug}-{int(time.time())}"
    r = redis_aio.from_url(
        _redis_url(), socket_connect_timeout=3.0, socket_timeout=10.0,
    )
    minio = get_storage()

    try:
        store = await Store.from_existing(debug_run_id, slug, r, minio)
        if not store.manifest:
            raise HTTPException(
                status_code=404,
                detail=f"no MinIO manifest for {slug!r} — run ingest first",
            )
        await store.finalize(extra={
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


# =============================================================================
# Snapshots
# =============================================================================
@router.post("/snapshot/{slug}")
async def debug_take_snapshot(
    slug: str, label: Optional[str] = None,
) -> dict:
    _entry_or_404(slug)
    return await snapshot.take(get_storage(), slug, label=label)


@router.get("/snapshots/{slug}")
async def debug_list_snapshots(slug: str) -> dict:
    _entry_or_404(slug)
    return {
        "slug": slug,
        "snapshots": await snapshot.list_snapshots(get_storage(), slug),
    }


@router.post("/restore/{slug}")
async def debug_restore_snapshot(slug: str, ts: str) -> dict:
    _entry_or_404(slug)
    try:
        return await snapshot.restore(get_storage(), slug, ts)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/snapshot/{slug}")
async def debug_delete_snapshot(slug: str, ts: str) -> dict:
    _entry_or_404(slug)
    return await snapshot.delete_snapshot(get_storage(), slug, ts)
