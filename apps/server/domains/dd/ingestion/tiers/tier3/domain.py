"""Tier 3 — pure helpers (URL slug + collision suffix)."""
from __future__ import annotations
from urllib.parse import urlparse

import re


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def collision_suffix(base: str, url: str) -> str:
    """Distinctive ancestor segment for a colliding slug (same design as
    Tier 2's `_claim_slug` stem: nearest URL ancestor whose slugified form
    differs from `base`, skipping trailing file segments). Additionally
    skips ancestors already contained in `base` (`streaming` inside
    `streaming-docs-by-langchain` would suffix redundantly — the next
    ancestor up, `langgraph`, is the actually distinctive one). `""` when
    the URL offers nothing distinctive — the caller falls back to a
    counter."""
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
        if parts and parts[-1].lower().endswith((".md", ".html", ".htm")):
            parts = parts[:-1]
        for seg in reversed(parts):
            cand = slugify(seg)
            if not cand or cand == base:
                continue
            if cand in base or base in cand:
                continue
            return cand
    except Exception:
        pass
    return ""
