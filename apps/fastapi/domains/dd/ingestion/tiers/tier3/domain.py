"""Tier 3 — pure helpers (URL slug)."""
from __future__ import annotations

import re


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"
