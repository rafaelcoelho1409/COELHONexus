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



_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_CURRENT_ALIASES = ("stable", "latest", "current")


def _version_rank(segment: str) -> tuple[int, tuple[int, ...]]:
    """How much a release folder is worth keeping: an alias for the current release (`stable`, `latest`, `current`) first, then the
    highest number, then the other aliases (`main`, `master`, `next`)."""
    low = segment.lower()
    if low in _CURRENT_ALIASES:
        return (3, (len(_CURRENT_ALIASES) - _CURRENT_ALIASES.index(low),))
    if low in ("main", "master", "next"):
        return (0, ())
    numbers = tuple(int(n) for n in re.findall(r"\d+", low))
    return (2, numbers)


def _version_segment(path: str) -> str | None:
    """The first segment of a URL path that names a release (`v5.21.0`, `2.0`, `stable`, `main`), not a year."""
    for segment in path.split("/"):
        if not segment or _YEAR_RE.match(segment):
            continue
        if segment.lower() in _CURRENT_ALIASES or segment.lower() in ("main", "master", "next") \
                or domains.dd.ingestion.post.patterns.VERSION_SEGMENT_RE.match(segment):
            return segment
    return None


def collapse_versions(urls: list[str]) -> list[str]:
    """One URL per page when a sitemap lists the same page under several releases (`/docs/stable/x`, `/docs/1.4/x`, `/docs/1.3/x`:
    DuckDB's lists five, Tier 3 deduplicated by exact URL only, so a run ingested every one of them). The release kept is the
    current one — `stable`, `latest`, `current` — else the highest-numbered. A page that exists under one release only, and
    a URL with no release folder, stay as they are; the list's order is kept."""
    groups: dict[str, list[tuple[int, str, str]]] = {}
    for position, url in enumerate(urls):
        parts = urlsplit(url)
        version = _version_segment(parts.path)
        if version is None:
            continue
        # Cut out the first occurrence of that segment: what is left is the page's identity across releases.
        stripped = parts.path.replace(f"/{version}/", "/", 1) if f"/{version}/" in parts.path else parts.path[: parts.path.rfind(f"/{version}")]
        groups.setdefault(f"{parts.netloc}{stripped}", []).append((position, url, version))
    drop: set[int] = set()
    for members in groups.values():
        if len({version for _p, _u, version in members}) < 2:
            continue
        keep = max(members, key = lambda m: _version_rank(m[2]))
        drop.update(position for position, _u, _v in members if position != keep[0])
    return [url for position, url in enumerate(urls) if position not in drop]


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
