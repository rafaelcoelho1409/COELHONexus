"""Tier 3 — pure helpers (URL slug + collision suffix + which sitemap URLs are in scope + markdown twins)."""
from __future__ import annotations
import domains
from . import params

import re
from urllib.parse import urlparse, urlsplit, urlunsplit



def markdown_twin_url(url: str) -> str | None:
    """Where a page's markdown twin lives on a site that publishes one: its URL with the trailing slash — or the `.html` —
    replaced by `.md` (Qdrant: `/documentation/search-patterns/` → `/documentation/search-patterns.md`). The query and
    fragment go. None for a site's root and for a URL that already is markdown."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path or path.lower().endswith(".md"):
        return None
    if path.lower().endswith((".html", ".htm")):
        path = path.rsplit(".", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{path}.md", "", ""))



def in_scope(url: str, path_filter: dict | None) -> bool:
    """A sitemap URL a Tier 3 run fetches, whatever the language it is asked for: not under a path for a language the
    docs are not being read in, and inside the catalog's `path_filter` (the default noise filter included)."""
    if domains.dd.ingestion.filters.patterns.NON_TARGET_LANGUAGE_PATH_RE.search(urlparse(url).path or ""):
        return False
    return domains.dd.ingestion.filters.domain.passes_path_filter(url, path_filter)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def suffixed_slug(base: str, suffix: str) -> str:
    """`base-suffix` within SLUG_MAX_CHARS, cutting `base` and never the suffix. (Cutting the joined string left two
    pages with the same 80-character title colliding forever — the counter was the part that got cut — and the run
    spun in the collision loop without ever awaiting.)"""
    tail = f"-{suffix}"
    return f"{base[: max(1, params.SLUG_MAX_CHARS - len(tail))].rstrip('-')}{tail}"


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
