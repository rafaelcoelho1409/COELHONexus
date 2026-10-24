"""Step 1 of the pipeline. Reads sources.yaml; tier priority:
llms_full > llms_txt > sitemap > docs > github."""
import domains
from . import schemas

from fastapi import APIRouter


router = APIRouter()


@router.get("")
def list_catalog() -> list[dict]:
    """Re-read every request so YAML edits land without a pod restart.
    Each entry carries its resolved `tier` (1-5, None when sourceless) so
    the Catalog UI can facet-filter without a second round-trip."""
    out = []
    for e in domains.dd.resolver.service.load_catalog():
        best = domains.dd.resolver.domain.pick_best_source(e)
        out.append({**e, "tier": best["tier"] if best else None})
    return out


@router.get("/{slug}")
def resolve_one(entry: schemas.CatalogEntry) -> dict:
    return {**entry, "best_source": domains.dd.resolver.domain.pick_best_source(entry)}
