"""Tier 1 — pure helpers (manifest detection + host slug)."""
from __future__ import annotations

import re

from .params import MANIFEST_MAX_FENCES, MANIFEST_MIN_URL_LINES
from .patterns import FENCE_RE, MD_POINTER_RE, URL_LINE_RE


def looks_like_manifest(body: str) -> tuple[bool, dict]:
    fence_count = len(FENCE_RE.findall(body))
    url_count = len(URL_LINE_RE.findall(body))
    md_pointer_count = len(MD_POINTER_RE.findall(body))
    is_manifest = (
        fence_count < MANIFEST_MAX_FENCES
        and (url_count > MANIFEST_MIN_URL_LINES
             or md_pointer_count > MANIFEST_MIN_URL_LINES)
    )
    return is_manifest, {
        "fences": fence_count,
        "urls": url_count,
        "md_pointers": md_pointer_count,
    }


def host_slug(host: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return f"{s}-llms-full"[:120] or "llms-full"
