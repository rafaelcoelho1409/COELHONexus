"""Tier 4 — pure helpers (no I/O): slugify, seeder glob pattern, link extraction from an already-fetched HTML string, SPA-shell
heuristic, where the docs live on a host, how discovery sources merge, which discovered URLs are crawled, and Playwright run-config
assembly from already-constructed objects."""
from __future__ import annotations
import domains
from . import params, patterns

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup



def slugify(s: str) -> str:
    return patterns.NON_SLUG_RE.sub("-", s.lower()).strip("-")[: params.SLUG_MAX_CHARS] or params.SLUG_FALLBACK


def page_slug(title: str, url: str) -> str:
    """A page's slug: its title's, else its URL path's."""
    return slugify(title or urlparse(url).path)


def sub_slug(base_slug: str, suffix: str) -> str:
    """The slug of one piece a page was cut into (`{page}--{piece}`)."""
    return f"{base_slug}{params.SUB_SLUG_SEPARATOR}{suffix}"[: params.SUB_SLUG_MAX_CHARS]


def seed_pattern_for(path: str) -> str | None:
    """Subtree path → seeder glob. Bare root → None (full-domain search)."""
    cleaned = (path or "").rstrip("/")
    if not cleaned or cleaned in ("/", ""):
        return None
    return f"*{cleaned}*"


def docs_subtree(path: str) -> str:
    """The folder the docs live in, from the path of the URL the catalog gives: everything before its last `/` ("" for a host's
    root)."""
    raw_path = path or "/"
    subtree = raw_path.rsplit("/", 1)[0] if "/" in raw_path else ""
    return "" if subtree in ("/", "") else subtree


def docs_root(scheme: str, netloc: str, subtree: str) -> str:
    """The URL of the folder the docs live in, with its trailing slash — where a Sphinx site's `objects.inv` is."""
    root_path = subtree if subtree else "/"
    if not root_path.endswith("/"):
        root_path += "/"
    return f"{scheme}://{netloc}{root_path}"


def probe_candidates(docs_url: str) -> list[str]:
    """The places on a host to look for its docs (`/docs/`, `/stable/`, …) when the catalog gives only the host: empty when the
    URL already has a path."""
    parsed = urlparse(docs_url)
    if parsed.path and parsed.path.rstrip("/") not in ("", "/"):
        return []
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in params.DOCS_PROBES]


def extract_links(html: str, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return []
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(params.NON_PAGE_HREF_PREFIXES):
            continue
        full = urljoin(base_url, href)
        if not full.startswith(params.HTTP_SCHEMES):
            continue
        out.append(full.split("#", 1)[0])
    return out


def crawlable_link(link: str, host: str, subtree: str) -> bool:
    """Whether a link found while crawling is followed: on the docs' host, and inside the docs' folder."""
    p = urlparse(link)
    if (p.netloc or "").lower() != host:
        return False
    return not subtree or (p.path or "").startswith(subtree)


def merge_seeds(sources: list[list[str]]) -> tuple[list[str], dict[str, list[str]]]:
    """`(pages, anchors by page)` from the discovery sources, best first. Order is kept (sorting would alphabetize and break
    chapter order — the Bash GNU manual regression) and a page found by several sources comes once, at its best place.
    A `page#anchor` seed (mostly objects.inv `std:label` entities) collapses onto its base page — a fragment is never a page's
    identity — and the anchor is kept as the page's metadata: without this, asyncio-style corpora ingested ~9 near-duplicate
    overlapping slices per real page (65 of 73 asyncio pages were `#` fragments)."""
    seeds: list[str] = []
    anchors_by_page: dict[str, list[str]] = {}
    seen: set[str] = set()
    for source in sources:
        for u in source:
            base, _, frag = (u or "").partition("#")
            base = base.strip()
            if not base:
                continue
            if frag and frag not in anchors_by_page.get(base, []):
                anchors_by_page.setdefault(base, []).append(frag)
            if base not in seen:
                seen.add(base)
                seeds.append(base)
    return seeds, anchors_by_page


def page_identity(src_url: str, anchors_by_page: dict[str, list[str]]) -> tuple[str, list[str]]:
    """`(page URL, its anchors)` for a fetched piece. A piece's URL may itself carry an `#anchor` (the split emits one slice per
    anchor): the base page is the identity, and the anchor joins the page's metadata."""
    base, _, sub_frag = (src_url or "").partition("#")
    base = base.strip() or src_url
    anchors = list(anchors_by_page.get(base, []))
    if sub_frag and sub_frag not in anchors:
        anchors.append(sub_frag)
    return base, anchors


def wanted(
    url: str,
    host: str,
    path_filter: dict | None,
    *,
    polyglot: bool,
    language: str | None,
    allow: list[str],
    deny: list[str],
) -> bool:
    """Whether a discovered URL is crawled: on the docs' host, not under a path for a language the docs are not being read in,
    inside the catalog's path_filter (the default noise filter included), and in the language asked for."""
    filters = domains.dd.ingestion.filters
    if not filters.domain.same_host(url, host):
        return False
    if filters.patterns.NON_TARGET_LANGUAGE_PATH_RE.search(urlparse(url).path or ""):
        return False
    if not filters.domain.passes_path_filter(url, path_filter):
        return False
    return filters.domain.passes_language(url, polyglot = polyglot, language = language, allow = allow, deny = deny)


def oracle_gap(inventory_pages: set[str], filtered: list[str]) -> tuple[int, set[str], set[str]]:
    """How the crawl compares with the Sphinx inventory (the oracle of which pages exist): `(pages crawled, inventory pages not
    crawled, crawled pages not in the inventory)`."""
    crawled = {u.split("#", 1)[0] for u in filtered}
    return len(crawled), inventory_pages - crawled, crawled - inventory_pages


def should_escalate(failed: int, total: int) -> bool:
    """Whether the httpx pass failed so often (more than PHASE4A_FAIL_RATE_TRIGGER of the pages) that the failed pages go to the
    browser."""
    return bool(failed) and failed / max(1, total) > params.PHASE4A_FAIL_RATE_TRIGGER


def is_transient_nav_error(error: str) -> bool:
    """Whether a browser failure reads like a slow page rather than a missing one, so it is worth one more try with longer
    timeouts."""
    return any(marker in error for marker in params.TRANSIENT_NAV_ERRORS)


def fallback_markdown(result) -> str:
    """What crawl4ai itself made of a page (fit markdown, else raw markdown, else its cleaned HTML), for when the page's rendered
    HTML gave nothing through our own extractor."""
    md_obj = getattr(result, "markdown", None)
    body = ""
    if md_obj is not None:
        body = getattr(md_obj, "fit_markdown", "") or getattr(md_obj, "raw_markdown", "") or ""
    return body or getattr(result, "cleaned_html", "") or ""


def looks_like_spa_shell(body: str) -> bool:
    if not body or len(body) < params.SPA_BODY_MIN:
        return True
    no_script = patterns.SCRIPT_BLOCK_RE.sub("", body)
    no_style = patterns.STYLE_BLOCK_RE.sub("", no_script)
    visible = patterns.TAG_RE.sub(" ", no_style)
    if len(visible.strip()) < params.SPA_TEXT_MIN:
        return True
    return bool(patterns.SPA_ROOT_RE.search(body) or patterns.HYDRATED_SPA_RE.search(body))


def spa_sample(candidates: list[str]) -> list[str]:
    """The pages fetched to tell whether the site is a single-page app: the first few that are not the site's root (a root is
    often a landing page), or of the root alone when there is nothing else."""
    deep = [u for u in candidates if (urlparse(u).path or "").strip("/")]
    return (deep or candidates)[: params.SPA_SAMPLE_SIZE]


def is_spa_majority(bodies: list[str]) -> bool:
    """Whether most of the fetched sample are SPA shells. No body at all (every fetch failed) counts as yes: biased to safety,
    since the browser will retry with a browser fingerprint."""
    if not bodies:
        return True
    return sum(1 for b in bodies if looks_like_spa_shell(b)) >= (len(bodies) // 2 + 1)


def build_run_configs(cfg_cls, cache_mode, lxml_strategy, md_generator):
    common = {
        "cache_mode": cache_mode.BYPASS,
        "wait_until": params.RUN_WAIT_UNTIL,
        "wait_for": params.RUN_WAIT_FOR,
        "word_count_threshold": params.RUN_WORD_COUNT_THRESHOLD,
        "excluded_tags": list(params.RUN_EXCLUDED_TAGS),
        "exclude_external_links": True,
        "scraping_strategy": lxml_strategy,
        "markdown_generator": md_generator,
        "stream": True,
        "max_retries": params.RUN_MAX_RETRIES,
        "verbose": False,
    }
    primary = cfg_cls(
        **common,
        delay_before_return_html = params.PRIMARY_RETURN_DELAY_S,
        page_timeout = params.PAGE_TIMEOUT_MS,
    )
    retry = cfg_cls(
        **common,
        delay_before_return_html = params.RETRY_RETURN_DELAY_S,
        page_timeout = params.RETRY_PAGE_TIMEOUT_MS,
    )
    return primary, retry
