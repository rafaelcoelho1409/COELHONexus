"""Sphinx / readthedocs.io comprehensive internal-page discovery for Tier 4.

readthedocs.io (and any Sphinx-built docs site) emits a complete page graph
across multiple DOM surfaces — the SIDEBAR nav AND the article body. RTD's
root ``sitemap.xml`` is per-VERSION only (e.g. adtk.readthedocs.io returns
just ``/en/stable/``, ``/en/latest/``); listing pages such as
``examples.html`` inline their children via Sphinx ``toctree::`` directives
that render in the page body, not the sidebar; and modern docs (scikit-learn,
JAX, pydata, JupyterBook) link sub-pages via ``sphinx-design`` clickable
cards. A sidebar-only extractor (sphinx_rtd_theme with the default
``collapse_navigation=True``) misses everything below the top level.

So we extract from BOTH surfaces and union the result:

  SIDEBAR (S1-S6) — theme-specific nav containers (sphinx_rtd_theme → Furo
                    → pydata/Book → alabaster → MkDocs Material → generic).

  BODY (B1-B3) — scoped to the article root (article.bd-article,
                 [role="main"], main, div.document):
       B1. ``div.toctree-wrapper a.reference.internal`` — the in-body
           toctree directive (the ADTK examples.html bug). Sphinx core
           emits this for every ``toctree::`` (visible or ``:hidden:``);
           stable across Sphinx 1.x–8.x and all themes. Also covers
           nbsphinx ``.. nbgallery::`` (same toctree under the hood) and
           MyST/JupyterBook ``tableofcontents``.
       B2. ``a.sd-stretched-link`` — sphinx-design clickable cards.
       B3. ``a.reference.internal[href]`` — inline ``:doc:`` cross-refs in
           narrative prose (final body-scoped catch-all).

Sphinx-wildcard auto-pages (``search.html``, ``genindex*``, ``py-modindex``,
``_modules/*``, ``_sources/*``, ``_static/*``, ``_images/*``,
``_downloads/*``) are filtered out — they're chrome, not content.

Returns ``[]`` for non-Sphinx pages so the Tier 4 generic BFS is unchanged.
"""
import asyncio
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)

_TIMEOUT_S = 30.0
_CONCURRENCY = 10
_USER_AGENT = "COELHONexus-DocsDistiller-Tier4/1.0"

# Theme-ordered sidebar selectors. First non-empty match wins so we don't
# leak body links via the generic ``a.reference.internal`` catch-all.
_SIDEBAR_SELECTORS = (
    "nav.wy-nav-side .wy-menu-vertical a.reference.internal",  # sphinx_rtd_theme
    "nav.bd-docs-nav a.reference.internal",                    # pydata-sphinx-theme
    "div.bd-sidebar a.reference.internal",                     # pydata variant
    "nav.sidebar-tree a.reference.internal",                   # Furo
    "div.sphinxsidebar a.reference.internal",                  # alabaster / classic
    "nav.bd-links a.reference.internal",                       # Book theme legacy
    "nav.md-nav a.md-nav__link",                               # MkDocs Material
)

# Article-body root candidates. Scoping body selectors to one of these
# excludes header / footer / sidebar chrome (incl. Edit-on-GitHub, version
# switcher, prev/next buttons in sphinx_rtd_theme's rst-footer-buttons).
_ARTICLE_ROOTS = (
    "article.bd-article",     # pydata-sphinx-theme
    "article[role='main']",   # Furo, modern themes
    "div[role='main']",       # sphinx_rtd_theme
    "main",                   # MkDocs Material, generic
    "div.document",           # alabaster
)

# Body-scoped selectors. UNION (not first-wins) — a page can have both an
# in-body toctree AND sphinx-design cards.
_BODY_SELECTORS = (
    "div.toctree-wrapper a.reference.internal",  # B1: in-body toctree
    "a.sd-stretched-link",                       # B2: sphinx-design cards
    "a.reference.internal",                      # B3: inline :doc: refs
)

_SKIP_HREF_PREFIXES = ("#", "javascript:", "mailto:", "tel:", "data:")

# Sphinx auto-generated pages and asset directories — exclude.
_EXCLUDE_PATH_RE = re.compile(
    r"(?:^|/)(?:search|genindex|genindex-[a-z]|py-modindex|modindex)\.html$"
    r"|/_(?:modules|sources|static|images|downloads)/"
)

# Non-page binary downloads — leave for asset crawlers.
_EXCLUDE_EXT_RE = re.compile(
    r"\.(?:ipynb|zip|tar\.gz|tgz|pdf|png|jpe?g|gif|svg|ico|"
    r"woff2?|ttf|otf|eot|mp4|webm|webp|css|js|map)$",
    re.IGNORECASE,
)

# If the landing page's sidebar already lists this many links, assume the
# full sidebar tree is rendered (non-collapsed theme) and skip the
# section-page sidebar expansion BFS.
_FULL_TREE_HINT = 25


def _normalize_href(href: str, base_url: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(_SKIP_HREF_PREFIXES):
        return None
    full = urljoin(base_url, href).split("#", 1)[0]
    if not full.startswith(("http://", "https://")):
        return None
    path = urlparse(full).path or ""
    if _EXCLUDE_PATH_RE.search(path) or _EXCLUDE_EXT_RE.search(path):
        return None
    return full


def _sidebar_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for sel in _SIDEBAR_SELECTORS:
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
    for sel in _ARTICLE_ROOTS:
        node = soup.select_one(sel)
        if node:
            root = node
            break
    if root is None:
        root = soup.body or soup
    out: list[str] = []
    seen: set[str] = set()
    for sel in _BODY_SELECTORS:
        for a in root.select(sel):
            full = _normalize_href(a.get("href"), base_url)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
    return out


def extract_internal_pages(html: str, base_url: str) -> dict[str, list[str]]:
    """Parse a Sphinx/MkDocs page and return ``{'sidebar': [...], 'body': [...]}``.
    Empty lists for both surfaces ⇒ page is not Sphinx/MkDocs."""
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
    """Backwards-compatible sidebar-only accessor (kept for fixture tests)."""
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
    """Comprehensive Sphinx/readthedocs discovery — sidebar + body.

    Reads the landing page from BOTH surfaces (sidebar nav and article body
    toctree-wrapper / sphinx-design cards / inline ``:doc:`` refs), then —
    unless the sidebar already lists a full tree — re-reads BOTH surfaces
    on each discovered section page so collapsed sub-trees (sphinx_rtd
    ``collapse_navigation``) and per-section in-body toctrees expand.

    Returns ``[]`` when the landing page has no sidebar AND no body links
    (i.e. not a Sphinx/MkDocs site) so the caller's generic BFS is unchanged.
    """

    def _in_scope(u: str) -> bool:
        p = urlparse(u)
        if (p.netloc or "").lower() != host:
            return False
        if subtree and not (p.path or "").startswith(subtree):
            return False
        return True

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _read(u: str) -> dict[str, list[str]]:
        async with sem:
            try:
                r = await client.get(
                    u, timeout=_TIMEOUT_S, follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
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

    discovered: set[str] = {landing_url}
    for src in (sidebar0, body0):
        discovered.update(u for u in src if _in_scope(u))

    # Fast path: landing sidebar lists a full tree already (non-collapsed
    # theme). We still keep landing body links from this pass.
    if len(sidebar0) >= _FULL_TREE_HINT:
        logger.info(
            f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
            f"(landing sidebar full tree, body added {len(body0)})"
        )
        return sorted(discovered)

    # Expand: re-read BOTH surfaces on each discovered section page.
    # ``max_pages`` is the hard cap on extra HTTP fetches (+1 for landing).
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
                    discovered.add(link)
                    new.append(link)
            for link in page["body"]:
                if link not in discovered and _in_scope(link):
                    discovered.add(link)
                    new.append(link)
                    body_recovered += 1
        frontier = new
        depth += 1

    logger.info(
        f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
        f"({fetched} pages read, depth≤{max_depth}, body recovered "
        f"{body_recovered} extras)"
    )
    return sorted(discovered)
