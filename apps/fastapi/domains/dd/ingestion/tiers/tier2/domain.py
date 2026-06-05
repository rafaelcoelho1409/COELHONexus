"""Tier 2 — pure helpers (index parse + slug + markdown-response detect)."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx

from .patterns import LINK_BARE_RE, LINK_MD_RE


def parse_index(body: str, base_url: str) -> list[tuple[str, str]]:
    """Return [(title, absolute_url), ...] from a llms.txt body. Tries the
    canonical markdown-link format first; then the bare-URL bullet format
    that Supervision (and likely others) use. Filters URLs to the same
    host as `base_url` so we don't try to ingest GitHub/PyPI/external
    meta-links that often appear in long-form llms.txt files. Dedupes
    while preserving first-occurrence order."""
    base_host = (urlparse(base_url).netloc or "").lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(title: str, url: str) -> None:
        url = urljoin(base_url, url.strip())
        host = (urlparse(url).netloc or "").lower()
        if base_host and host and host != base_host:
            return
        if url in seen:
            return
        seen.add(url)
        out.append((title.strip(), url))

    for m in LINK_MD_RE.finditer(body):
        _add(m.group(1), m.group(2))
    for m in LINK_BARE_RE.finditer(body):
        _add(m.group(1), m.group(2))
    return out


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def is_markdown_response(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return (
        "text/markdown" in ctype
        or "text/x-markdown" in ctype
        or resp.url.path.endswith(".md")
    )
