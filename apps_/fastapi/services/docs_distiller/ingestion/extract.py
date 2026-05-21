"""HTML → Markdown extraction.

Strips navigation/footer/aside chrome with BeautifulSoup, then converts
the remaining body with markdownify (ATX headings, fenced code blocks).
Returns clean markdown ready for splitting/dedup.
"""
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup
from markdownify import markdownify as _md


logger = logging.getLogger(__name__)


# Tags to strip outright before conversion. Covers the usual chrome that
# would otherwise leak into the markdown body: navs, sidebars, footers,
# noscript fallbacks, scripts, styles, forms, and SVG icons.
_CHROME_SELECTORS = (
    "script", "style", "noscript", "iframe", "form", "svg",
    "nav", "header", "footer", "aside",
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[role="complementary"]', '[role="search"]',
    ".nav", ".navigation", ".navbar", ".sidebar", ".menu",
    ".header", ".footer", ".breadcrumb",
    ".toc", ".tocify", ".table-of-contents",
    ".cookie", ".cookie-banner", ".cookies-banner",
    ".announce", ".announcement",
    ".search-form", ".searchbox",
)

# Common "main content" candidates — try these first before falling back to
# <body>. Many docs sites (Sphinx, mkdocs, Docusaurus, Nextra, GitBook,
# Mintlify) use one of these patterns.
_CONTENT_SELECTORS = (
    "main",
    'article',
    '[role="main"]',
    "#content", "#main", "#main-content", "#docs-content",
    ".content", ".main", ".main-content", ".docs-content",
    ".markdown-body", ".prose",
    ".article", ".post", ".doc-content",
)


def _strip_chrome(soup: BeautifulSoup) -> None:
    for sel in _CHROME_SELECTORS:
        for el in soup.select(sel):
            el.decompose()


def _find_content_root(soup: BeautifulSoup):
    for sel in _CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def html_to_markdown(html: str, source_url: Optional[str] = None) -> str:
    """Convert HTML to markdown. Returns "" on empty/garbage input.

    Behavior: parse → strip chrome → pick best content root → convert.
    Markdownify settings: ATX headings (## style), fenced code blocks
    (triple-backtick), strip <a> empties.
    """
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.info(f"[extract] lxml parse failed for {source_url}: {e}; "
                    f"falling back to html.parser")
        soup = BeautifulSoup(html, "html.parser")

    _strip_chrome(soup)
    root = _find_content_root(soup)

    md = _md(
        str(root),
        heading_style="ATX",
        code_language="",
        bullets="*-+",
        strip=["script", "style"],
    )
    # markdownify can emit long runs of blank lines from divs/spans; collapse
    return _collapse_blank_lines(md).strip()


def _collapse_blank_lines(s: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", s)


def extract_title(html: str) -> str:
    """Best-effort page title from <title> or first h1. Empty string on
    failure."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:200]
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)[:200]
    return ""
