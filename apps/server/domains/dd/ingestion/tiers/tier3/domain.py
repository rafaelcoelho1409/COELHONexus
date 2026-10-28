"""Tier 3 — pure helpers (URL slug + collision suffix + which sitemap URLs are in scope + markdown twins)."""
from __future__ import annotations
import domains
from . import params, patterns

from urllib.parse import urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup



def markdown_twin_url(url: str) -> str | None:
    """Where a page's markdown twin lives on a site that publishes one: its URL with the trailing slash — or the `.html` —
    replaced by `.md` (Qdrant: `/documentation/search-patterns/` → `/documentation/search-patterns.md`). The query and
    fragment go. None for a site's root and for a URL that already is markdown."""
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    if not path or path.lower().endswith(params.MARKDOWN_SUFFIX):
        return None
    if path.lower().endswith(params.HTML_SUFFIXES):
        path = path.rsplit(".", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, f"{path}{params.MARKDOWN_SUFFIX}", "", ""))


def _version_rank(segment: str) -> tuple[int, tuple[int, ...]]:
    """How much a release folder is worth keeping: an alias for the current release (`stable`, `latest`, `current`) first, then the
    highest number, then the other aliases (`main`, `master`, `next`)."""
    low = segment.lower()
    if low in params.CURRENT_ALIASES:
        return (3, (len(params.CURRENT_ALIASES) - params.CURRENT_ALIASES.index(low),))
    if low in params.BRANCH_ALIASES:
        return (0, ())
    numbers = tuple(int(n) for n in patterns.DIGITS_RE.findall(low))
    return (2, numbers)


def _version_segment(path: str) -> str | None:
    """The first segment of a URL path that names a release (`v5.21.0`, `2.0`, `stable`, `main`), not a year."""
    for segment in path.split("/"):
        if not segment or patterns.YEAR_RE.match(segment):
            continue
        if segment.lower() in params.CURRENT_ALIASES or segment.lower() in params.BRANCH_ALIASES \
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


def parse_sitemap(xml: str) -> tuple[list[str], list[tuple[str, str]]]:
    """`(nested sitemaps, [(page URL, lastmod), ...])` of one sitemap document, each in document order. `lastmod` is "" when the
    sitemap carries none — callers must treat "" as unknown, never as old."""
    try:
        soup = BeautifulSoup(xml or "", params.SITEMAP_PARSER)
    except Exception:  # noqa: BLE001
        soup = BeautifulSoup(xml or "", params.FALLBACK_PARSER)
    nested = [loc.text.strip() for sm in soup.find_all("sitemap") if (loc := sm.find("loc")) and loc.text]
    pages: list[tuple[str, str]] = []
    for u in soup.find_all("url"):
        loc = u.find("loc")
        if loc and loc.text:
            lm = u.find("lastmod")
            pages.append((loc.text.strip(), (lm.text.strip() if lm and lm.text else "")[: params.LASTMOD_MAX_CHARS]))
    return nested, pages


def unique_in_order(urls: list[str]) -> list[str]:
    """Each URL once, at the place it first appears."""
    return list(dict.fromkeys(urls))


def wanted(
    url: str,
    path_filter: dict | None,
    *,
    polyglot: bool,
    language: str | None,
    allow: list[str],
    deny: list[str],
) -> bool:
    """Whether a sitemap URL is fetched: in scope (`in_scope`), and — for a framework with several language editions or a
    language filter — inside the language's allow / deny globs."""
    return in_scope(url, path_filter) and domains.dd.ingestion.filters.domain.passes_language(
        url, polyglot = polyglot, language = language, allow = allow, deny = deny,
    )


def twin_sample(urls: list[str]) -> list[str]:
    """The pages to ask for a markdown twin to learn whether the site publishes them: TWIN_PROBE_SAMPLE pages spread evenly over
    `urls`. Empty when there are too few pages for the probe to prove anything (fewer than TWIN_PROBE_MIN_OK)."""
    size = min(params.TWIN_PROBE_SAMPLE, len(urls))
    if size < params.TWIN_PROBE_MIN_OK:
        return []
    step = len(urls) / size
    return [urls[int(i * step)] for i in range(size)]


def usable_twin(status_code: int, text: str) -> bool:
    """Whether the answer to a request for a page's markdown twin is a twin: a 200, long enough, and not an HTML page (a site
    that answers 200 to anything)."""
    return (
        status_code == 200
        and len(text.encode("utf-8")) >= params.MIN_OK_BYTES
        and not domains.dd.ingestion.tiers.tier1.domain.looks_like_html(text)
    )


def free_slug(owners: dict[str, str], base: str, link: str) -> str:
    """The slug `link` takes in a run where `owners` ({slug: the link that has it}) is already spoken for: the bare `base` when
    nobody else has it, else `base` with the nearest distinctive URL ancestor (`collision_suffix`), a counter as the last
    resort. Same-topic pages across products (langchain / langgraph / deepagents `streaming` ×3) and tutorial-vs-reference pairs
    (fastapi `middleware` ×2) are the live cases. Keys were already unique (idx-prefixed): this only cleans manifests and
    explorer titles."""
    if base not in owners or owners[base] == link:
        return base
    stem = collision_suffix(base, link) or params.SLUG_STEM_FALLBACK
    candidate = suffixed_slug(base, stem)
    i = 2
    while candidate in owners and owners[candidate] != link:
        candidate = suffixed_slug(base, f"{stem}-{i}")
        i += 1
    return candidate


def in_scope(url: str, path_filter: dict | None) -> bool:
    """A sitemap URL a Tier 3 run fetches, whatever the language it is asked for: not under a path for a language the
    docs are not being read in, and inside the catalog's `path_filter` (the default noise filter included)."""
    if domains.dd.ingestion.filters.patterns.NON_TARGET_LANGUAGE_PATH_RE.search(urlparse(url).path or ""):
        return False
    return domains.dd.ingestion.filters.domain.passes_path_filter(url, path_filter)


def slugify(s: str) -> str:
    return patterns.NON_SLUG_RE.sub("-", s.lower()).strip("-")[: params.SLUG_MAX_CHARS] or params.SLUG_FALLBACK


def page_slug(title: str, url: str) -> str:
    """A page's slug: its title's, else its URL path's."""
    return slugify(title or urlparse(url).path)


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
        if parts and parts[-1].lower().endswith((params.MARKDOWN_SUFFIX, *params.HTML_SUFFIXES)):
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
