"""Resolver — curated framework catalog from sources.yaml.

Reads the catalog, injects slugs, exposes `_index_by_slug()` used by
ingestion dispatch, planner off_topic, and the resolver HTTP endpoints.

Tier priority (highest -> lowest): llms_full > llms_txt > sitemap > docs > github
"""
import re
import unicodedata
from collections import Counter
from pathlib import Path

import yaml


SOURCES_PATH = Path(__file__).resolve().parents[2] / "shared" / "sources.yaml"

TIER_ORDER = ("llms_full", "llms_txt", "sitemap", "docs", "github")


def slugify(name: str) -> str:
    """URL-safe slug derived from a framework name.

    Rules: NFKD-normalize -> strip non-ASCII -> lowercase -> replace any run of
    non-[a-z0-9] with a single hyphen -> trim leading/trailing hyphens.

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
