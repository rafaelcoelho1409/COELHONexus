"""Backfill — pure helpers. No I/O."""
from __future__ import annotations

from typing import Optional


def parse_page_key(key: str) -> Optional[tuple[int, str]]:
    """`ingestion/{slug}/pages/{idx:04d}-{page_slug}.md` → (idx, page_slug)."""
    fname = key.rsplit("/", 1)[-1].removesuffix(".md")
    if "-" not in fname:
        return None
    head, _, page_slug = fname.partition("-")
    try:
        idx = int(head)
    except ValueError:
        return None
    return idx, page_slug
