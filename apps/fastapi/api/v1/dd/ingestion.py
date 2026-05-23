"""Docs Distiller — Library router (persistent ingestion artifacts).

Read-only view of the per-framework MinIO content. Anything written here
survives Redis TTL — it's the canonical, deduplicable corpus that future
per-experience-level synth (senior / mid / junior) will reuse without
re-downloading.

  GET /api/v1/docs-distiller/ingestion
      -> summary list of every framework whose ingestion has been
        finalized (sidebar data source in FastHTML).

  GET /api/v1/docs-distiller/ingestion/{slug}/manifest
      -> full manifest dict (entries + ingest metadata).

  GET /api/v1/docs-distiller/ingestion/{slug}/pages/{idx}
      -> raw markdown body for one page.
"""
from fastapi import APIRouter, HTTPException

from domains.dd.ingestion.storage import (
    framework_prefix,
    get_storage,
)
from domains.dd.ingestion.storage import (
    read_framework_manifest,
    read_framework_page,
)

from domains.dd.resolver import _index_by_slug


router = APIRouter()


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


@router.delete("/{slug}")
async def delete_framework(slug: str) -> dict:
    """Wipe every MinIO object under `ingestion/{slug}/` — manifest,
    page bodies, snapshots. After this the slug looks brand-new to the
    cached-check, so the next `POST /runs {slug}` will re-ingest from
    scratch. `slug` is taken literally — passing a run_id wipes orphan
    content from the pre-fix-keyed runs."""
    deleted = await get_storage().delete_prefix(framework_prefix(slug))
    return {"slug": slug, "deleted": deleted}
