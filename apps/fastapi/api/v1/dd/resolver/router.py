"""Step 1 of the pipeline. Reads sources.yaml; tier priority:
llms_full > llms_txt > sitemap > docs > github."""
from fastapi import APIRouter

from domains.dd.resolver import load_catalog, pick_best_source

from ..dependencies import CatalogEntry


router = APIRouter()


@router.get("")
def list_catalog() -> list[dict]:
    """Re-read every request so YAML edits land without a pod restart."""
    return load_catalog()


@router.get("/{slug}")
def resolve_one(entry: CatalogEntry) -> dict:
    return {**entry, "best_source": pick_best_source(entry)}
