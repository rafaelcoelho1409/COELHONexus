"""Tier 1 — pure helpers (manifest detection + host slug)."""
from __future__ import annotations
from . import params, patterns

import re



def looks_like_manifest(body: str) -> tuple[bool, dict]:
    fence_count = len(patterns.FENCE_RE.findall(body))
    url_count = len(patterns.URL_LINE_RE.findall(body))
    md_pointer_count = len(patterns.MD_POINTER_RE.findall(body))
    is_manifest = (
        fence_count < params.MANIFEST_MAX_FENCES
        and (url_count > params.MANIFEST_MIN_URL_LINES
             or md_pointer_count > params.MANIFEST_MIN_URL_LINES)
    )
    return is_manifest, {
        "fences": fence_count,
        "urls": url_count,
        "md_pointers": md_pointer_count,
    }


def host_slug(host: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return f"{s}-llms-full"[:120] or "llms-full"
