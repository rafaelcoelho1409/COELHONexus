"""Sphinx / readthedocs.io discovery — union of sidebar nav + article body.

RTD root sitemaps are per-version only; sphinx_rtd_theme's default
collapse_navigation hides anything below the top level; modern docs link
sub-pages via in-body toctrees (`div.toctree-wrapper`), sphinx-design cards
(`a.sd-stretched-link`), or inline `:doc:` refs. Sidebar OR body alone
misses too much. Returns [] for non-Sphinx pages so Tier 4 BFS proceeds.
"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .params import (
    ARTICLE_ROOTS,
    BODY_SELECTORS,
    NAV_CONCURRENCY,
    SIDEBAR_SELECTORS,
    SKIP_HREF_PREFIXES,
    SPHINX_USER_AGENT,
    TIMEOUT_S,
)
from .patterns import EXCLUDE_EXT_RE, EXCLUDE_PATH_RE


logger = logging.getLogger(__name__)

# Landing sidebar ≥ this many links → assume full tree rendered (skip expansion BFS).
_FULL_TREE_HINT = 25


def _normalize_href(href: str, base_url: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(SKIP_HREF_PREFIXES):
        return None
    full = urljoin(base_url, href).split("#", 1)[0]
    if not full.startswith(("http://", "https://")):
        return None
    path = urlparse(full).path or ""
    if EXCLUDE_PATH_RE.search(path) or EXCLUDE_EXT_RE.search(path):
        return None
    return full


def _sidebar_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for sel in SIDEBAR_SELECTORS:
        anchors = soup.select(sel)
        if not anchors:
            continue
        for a in anchors:
            full = _normalize_href(a.get("href"), base_url)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
        break  # first-matching theme wins
    return out


def _body_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    root = None
    for sel in ARTICLE_ROOTS:
        node = soup.select_one(sel)
        if node:
            root = node
            break
    if root is None:
        root = soup.body or soup
    out: list[str] = []
    seen: set[str] = set()
    for sel in BODY_SELECTORS:
        for a in root.select(sel):
            full = _normalize_href(a.get("href"), base_url)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
    return out


def extract_internal_pages(html: str, base_url: str) -> dict[str, list[str]]:
    """Sphinx/MkDocs surfaces → {sidebar, body}. Both empty ⇒ not Sphinx/MkDocs."""
    if not html:
        return {"sidebar": [], "body": []}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    return {
        "sidebar": _sidebar_links(soup, base_url),
        "body": _body_links(soup, base_url),
    }


def extract_sidebar_links(html: str, base_url: str) -> list[str]:
    """Sidebar-only accessor (kept for fixture tests)."""
    return extract_internal_pages(html, base_url)["sidebar"]


async def discover_via_toctree(
    landing_url: str,
    *,
    host: str,
    subtree: str,
    client: httpx.AsyncClient,
    max_depth: int = 1,
    max_pages: int = 50,
) -> list[str]:
    """Sphinx/readthedocs discovery from sidebar + body, BFS-expanded on
    section pages unless the landing sidebar already has a full tree.
    Returns [] for non-Sphinx pages so the generic BFS is unchanged.
    """

    def _in_scope(u: str) -> bool:
        p = urlparse(u)
        if (p.netloc or "").lower() != host:
            return False
        if subtree and not (p.path or "").startswith(subtree):
            return False
        return True

    sem = asyncio.Semaphore(NAV_CONCURRENCY)

    async def _read(u: str) -> dict[str, list[str]]:
        async with sem:
            try:
                r = await client.get(
                    u, timeout=TIMEOUT_S, follow_redirects=True,
                    headers={"User-Agent": SPHINX_USER_AGENT},
                )
            except Exception:
                return {"sidebar": [], "body": []}
        if r.status_code != 200:
            return {"sidebar": [], "body": []}
        if "html" not in (r.headers.get("content-type") or "").lower():
            return {"sidebar": [], "body": []}
        return extract_internal_pages(r.text or "", str(r.url))

    landing = await _read(landing_url)
    sidebar0 = landing["sidebar"]
    body0 = landing["body"]
    if not sidebar0 and not body0:
        return []  # not a Sphinx/MkDocs site

    # Insertion-order dedup — preserves toctree (author chapter) order.
    # Sorting would alphabetize (Bash html_node would ingest Aliases before Shell-Operation).
    discovered: dict[str, None] = {landing_url: None}
    for src in (sidebar0, body0):
        for u in src:
            if u not in discovered and _in_scope(u):
                discovered[u] = None

    # Fast path: landing sidebar already lists a full tree.
    if len(sidebar0) >= _FULL_TREE_HINT:
        logger.info(
            f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
            f"(landing sidebar full tree, body added {len(body0)})"
        )
        return list(discovered.keys())

    # Expand both surfaces on each section page. max_pages caps extra HTTP fetches.
    frontier = [u for u in discovered if u != landing_url]
    fetched = 1
    body_recovered = 0
    depth = 1
    while frontier and depth <= max_depth and fetched < max_pages:
        budget = max(0, max_pages - fetched)
        batch = frontier[:budget]
        fetched += len(batch)
        results = await asyncio.gather(*(_read(u) for u in batch))
        new: list[str] = []
        for page in results:
            for link in page["sidebar"]:
                if link not in discovered and _in_scope(link):
                    discovered[link] = None
                    new.append(link)
            for link in page["body"]:
                if link not in discovered and _in_scope(link):
                    discovered[link] = None
                    new.append(link)
                    body_recovered += 1
        frontier = new
        depth += 1

    logger.info(
        f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
        f"({fetched} pages read, depth≤{max_depth}, body recovered "
        f"{body_recovered} extras)"
    )
    return list(discovered.keys())
