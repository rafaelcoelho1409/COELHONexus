"""Resolver — Step 1 of the Docs Distiller pipeline.

Reads the curated catalog from sources.yaml and exposes two endpoints:

  GET /api/v1/docs-distiller/resolver
    -> list the full catalog (every entry, with `slug` injected)

  GET /api/v1/docs-distiller/resolver/{slug}
    -> one entry plus `best_source` = the tier-picked URL the pipeline's
      Ingestion stage will try first

Tier priority (highest -> lowest): llms_full > llms_txt > sitemap > docs > github
"""
from fastapi import APIRouter, HTTPException

from domains.dd.resolver import _index_by_slug, _load_catalog, _pick_best_source

router = APIRouter()


@router.get("")
def list_catalog() -> list[dict]:
    """Full catalog with slugs injected. Re-read every request so YAML
    edits land without a pod restart."""
    return _load_catalog()


@router.get("/{slug}")
def resolve_one(slug: str) -> dict:
    """One entry + `best_source` (the tier-picked URL for Ingestion)."""
    entry = _index_by_slug().get(slug)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"framework '{slug}' not found")
    return {**entry, "best_source": _pick_best_source(entry)}
