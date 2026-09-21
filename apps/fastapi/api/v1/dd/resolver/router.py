"""Step 1 of the pipeline. Reads sources.yaml; tier priority:
llms_full > llms_txt > sitemap > docs > github."""
import domains
from fastapi import APIRouter

from . import schemas


router = APIRouter()


@router.get("")
def list_catalog() -> list[dict]:
    """Re-read every request so YAML edits land without a pod restart."""
    return domains.dd.resolver.service.load_catalog()


@router.get("/{slug}")
def resolve_one(entry: schemas.CatalogEntry) -> dict:
    return {**entry, "best_source": domains.dd.resolver.domain.pick_best_source(entry)}
