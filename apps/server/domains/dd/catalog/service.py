"""YAML catalog loader + slug index."""
from __future__ import annotations
from . import domain, params

from collections import Counter

import yaml


def load_catalog() -> list[dict]:
    """Read sources.yaml, inject `slug` per entry, fail loudly on collisions."""
    with open(params.SOURCES_PATH) as f:
        data = yaml.safe_load(f) or {}
    entries = data.get("frameworks", [])

    out: list[dict] = []
    for e in entries:
        out.append({**e, "slug": domain.slugify(e["name"])})

    dupes = {s: n for s, n in Counter(e["slug"] for e in out).items() if n > 1}
    if dupes:
        raise RuntimeError(f"slug collisions in sources.yaml: {dupes}")

    return out


def index_by_slug() -> dict[str, dict]:
    return {e["slug"]: e for e in load_catalog()}
