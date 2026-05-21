"""Resolver — Step 1 of the Docs Distiller pipeline.

Reads the curated catalog from sources.yaml and exposes two endpoints:

  GET /api/v1/docs-distiller/resolver
    → list the full catalog (every entry, with `slug` injected)

  GET /api/v1/docs-distiller/resolver/{slug}
    → one entry plus `best_source` = the tier-picked URL the pipeline's
      Ingestion stage will try first

Tier priority (highest → lowest): llms_full > llms_txt > sitemap > docs > github
"""
import re
import unicodedata
from collections import Counter
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException

router = APIRouter()

# /app/routers/v1/docs_distiller/resolver.py → parents[3] = /app
SOURCES_PATH = Path(__file__).resolve().parents[3] / "files" / "sources.yaml"

# Order = priority. Leftmost is highest. Drives `best_source` selection and,
# downstream, which Ingestion tier the pipeline tries first.
TIER_ORDER = ("llms_full", "llms_txt", "sitemap", "docs", "github")


def slugify(name: str) -> str:
    """URL-safe slug derived from a framework name.

    Rules: NFKD-normalize → strip non-ASCII → lowercase → replace any run of
    non-[a-z0-9] with a single hyphen → trim leading/trailing hyphens.

    Verified collision-free across all 115 entries in sources.yaml; a
    runtime check in `_load_catalog()` will raise loudly if a future YAML
    edit introduces a clash.
    """
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _pick_best_source(entry: dict) -> dict | None:
    """Return {tier, kind, url} for the highest-priority source present.
    None when an entry has no source URL fields at all (shouldn't happen
    in practice but kept defensive)."""
    for i, kind in enumerate(TIER_ORDER, start=1):
        url = entry.get(kind)
        if url:
            return {"tier": i, "kind": kind, "url": url}
    return None


def _load_catalog() -> list[dict]:
    """Read sources.yaml, inject `slug` into each entry, fail loudly on
    slug collisions."""
    with open(SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}
    entries = data.get("frameworks", [])

    out: list[dict] = []
    for e in entries:
        out.append({**e, "slug": slugify(e["name"])})

    dupes = {s: n for s, n in Counter(e["slug"] for e in out).items() if n > 1}
    if dupes:
        raise RuntimeError(f"slug collisions in sources.yaml: {dupes}")

    return out


def _index_by_slug() -> dict[str, dict]:
    return {e["slug"]: e for e in _load_catalog()}


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
