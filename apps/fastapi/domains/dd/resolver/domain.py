"""Pure helpers — no I/O."""
from __future__ import annotations

import re
import unicodedata

from .params import TIER_ORDER


def slugify(name: str) -> str:
    """URL-safe slug from a framework name. NFKD → ASCII-strip → lowercase →
    collapse non-[a-z0-9] to hyphens → trim. Collision-free across the 115
    sources.yaml entries; `load_catalog` raises on future collisions."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def pick_best_source(entry: dict) -> dict | None:
    """{tier, kind, url} for the highest-priority source present.
    None when no source URL fields are set (defensive — shouldn't happen)."""
    for i, kind in enumerate(TIER_ORDER, start = 1):
        url = entry.get(kind)
        if url:
            return {"tier": i, "kind": kind, "url": url}
    return None
