"""
HTML → Markdown extractor — four-stage pipeline.

Stage 1 — HTML pre-clean (trafilatura.tree_cleaning, BS4 fallback)
  Strip <nav>/<header>/<footer>/<aside>, ad/social classes, scripts/styles.
  Trafilatura's curated chrome list is preferred when available; bs4 fallback
  covers the same structural patterns more conservatively.

Stage 2 — HTML → Markdown (Crawl4AI DefaultMarkdownGenerator, no pruner)
  Code-preserving conversion (mark_code, handle_code_in_pre, body_width=0).

Stage 3 — Markdown post-clean (regex cosmetic)
  Strip heading anchor markers ([¶], [​], [#]), empty links, logo image
  links, "Skip to main content" / "Edit on GitHub"-style chrome phrases,
  collapse multi-blank-lines. Catches per-platform leftovers that survive
  HTML-level pruning since they're inside heading text or already inline.

Stage 4 — Quality re-gate
  Drop pages with link-text-ratio > 70% or <200 chars after cleanup.
  Catches blog/index pages that are 100% navigation chrome.

HISTORY (2026-04-28):
  Early session: swapped trafilatura.extract() → Crawl4AI's
                 DefaultMarkdownGenerator for code-block preservation
                 (trafilatura's extract had issue adbar/trafilatura#489
                 — code blocks losing indentation and language tags).
  Late session:  empirical testing showed nav/sidebar/footer chrome
                 leaking into the corpus (~30-40% of small pages were
                 chrome). Re-introduced trafilatura BUT only the
                 tree_cleaning() HTML pre-processor — the part where
                 trafilatura is best-in-class. Extraction stays with
                 Crawl4AI. Two distinct functions; right tool per job.

WHY THIS BEATS BOTH ALTERNATIVES:
  vs. old trafilatura.extract():
    + Same chrome stripping (same library, same function internally)
    + Code blocks 100% balanced (Crawl4AI extractor) vs ~85% (trafilatura)
    + No article-shape extraction → API reference sidebars / code-heavy
      pages preserved instead of stub-stripped
  vs. PruningContentFilter (the other content filter we tried):
    + No tag-density heuristic that mistakes code-dense pages for noise
    + 14% of MLflow pages had ZERO code fences with the pruner; with
      tree_cleaning chrome strip, code-heavy pages preserve all code

See docs/KNOWLEDGE-DISTILLER-MARKDOWN-EXTRACTOR-MIGRATION.md.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Singleton — Crawl4AI DefaultMarkdownGenerator (Stage 2 converter)
# =============================================================================
_MD_GEN = None


def _ensure_initialized() -> None:
    """Lazy import + init of Crawl4AI's DefaultMarkdownGenerator. Crawl4AI's
    content_filter_strategy module pulls a heavy dependency chain; defer
    loading until first use."""
    global _MD_GEN
    if _MD_GEN is not None:
        return
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    # No `content_filter` — pruning disabled. Returns raw_markdown
    # (direct HTML→md transform; all content preserved including code
    # blocks). Code-block formatting options retained.
    _MD_GEN = DefaultMarkdownGenerator(
        options = {
            "mark_code": True,            # explicit fenced-block detection
            "handle_code_in_pre": True,   # preserve indentation in <pre>
            "body_width": 0,              # no line wrapping
            "escape_html": False,         # don't double-escape HTML in code
            "wrap": False,                # no auto-wrap of paragraphs
            "ignore_links": False,        # keep cross-references
        },
    )


# =============================================================================
# Stage 1 — HTML pre-clean (chrome stripping)
# =============================================================================
# Docs-platform-specific in-page TOC containers — patterns trafilatura's
# news-article-tuned default list doesn't know about. Stripped via XPath
# BEFORE handing the tree to trafilatura.
_DOCS_TOC_XPATHS = (
    # Docusaurus (MLflow, LangChain, etc.)
    '//*[contains(concat(" ", normalize-space(@class), " "), " theme-doc-toc-mobile ")]',
    '//*[contains(concat(" ", normalize-space(@class), " "), " theme-doc-toc-desktop ")]',
    '//*[contains(@class, "tableOfContents")]',
    # Sphinx
    '//*[contains(concat(" ", normalize-space(@class), " "), " contents ") and contains(concat(" ", normalize-space(@class), " "), " local ")]',
    '//*[contains(concat(" ", normalize-space(@class), " "), " sphinxsidebarwrapper ")]',
    # Material for MkDocs
    '//*[contains(@class, "md-nav--secondary")]',
    # ARIA role for in-page TOC (some Docusaurus + custom themes)
    '//*[@role="doc-toc"]',
    # Common "on this page" widget patterns
    '//*[@id="on-this-page"]',
    '//*[@id="page-toc"]',
    '//*[contains(@class, "on-this-page")]',
)


def _strip_chrome_html(html: str) -> str:
    """
    Pre-process HTML to remove navigation chrome (nav/header/footer/aside,
    sidebars, ads, scripts/styles) before handing to the markdown converter.

    Primary: trafilatura.htmlprocessing.tree_cleaning() — curated
             battle-tested chrome list (50+ patterns, maintained upstream).
             Operates on lxml tree; fast (~10-50 ms for typical docs page).
             Does NOT touch <pre>/<code> tags.

             Pre-strip step: BEFORE handing to trafilatura, we apply our
             own XPath patterns for docs-platform-specific TOC containers
             (Docusaurus theme-doc-toc-*, Sphinx contents.local, MkDocs
             md-nav--secondary, etc.). Trafilatura is news-article tuned
             and doesn't know about these patterns.

    Fallback: BeautifulSoup-based stripper covering the same structural
              patterns + docs-platform classes. Activates when trafilatura
              is unavailable OR raises.

    Defensive return: on any failure, returns the input unchanged so
    downstream conversion still runs (extra chrome in output > zero output).
    """
    # ----------- Primary path: trafilatura tree_cleaning -----------
    try:
        from lxml import html as lxml_html
        from trafilatura.htmlprocessing import tree_cleaning
        from trafilatura.settings import Extractor

        tree = lxml_html.fromstring(html)

        # Pre-strip: docs-platform-specific in-page TOCs (trafilatura
        # doesn't know about these — news-article focused).
        for xpath in _DOCS_TOC_XPATHS:
            for el in tree.xpath(xpath):
                parent = el.getparent()
                if parent is not None:
                    parent.remove(el)

        # Then trafilatura's curated general chrome list.
        opts = Extractor()
        cleaned = tree_cleaning(tree, opts)
        return lxml_html.tostring(cleaned, encoding = "unicode")
    except Exception as e:
        logger.debug(
            f"[md-gen] trafilatura tree_cleaning unavailable ({e}); "
            f"using bs4 fallback"
        )

    # ----------- Fallback path: bs4 stripper -----------
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Structural HTML5 chrome
        for tag in soup.find_all((
            "nav", "header", "footer", "aside",
            "script", "style", "noscript", "template",
        )):
            tag.decompose()

        # ARIA-role containers
        for sel in (
            "[role=navigation]", "[role=banner]", "[role=contentinfo]",
            "[role=complementary]", "[role=search]", "[role=doc-toc]",
        ):
            for el in soup.select(sel):
                el.decompose()

        # Platform-specific class/id patterns
        for sel in (
            ".sidebar", ".navbar", ".breadcrumb", ".breadcrumbs",
            ".skip-to-content", ".skip-link",
            ".sphinxsidebar", ".sphinxsidebarwrapper", ".related", ".relations",  # Sphinx
            ".theme-doc-sidebar-container",                                        # Docusaurus sidebar
            ".theme-doc-toc-mobile", ".theme-doc-toc-desktop",                     # Docusaurus in-page TOC
            '[class*="tableOfContents"]',                                          # Docusaurus React component
            ".md-sidebar", ".md-search", ".md-header", ".md-nav--secondary",       # MkDocs Material
            "#sidebar", "#navbar", "#footer-nav", "#on-this-page", "#page-toc",
            ".on-this-page",
        ):
            for el in soup.select(sel):
                el.decompose()

        return str(soup)
    except Exception as e:
        logger.warning(f"[md-gen] bs4 fallback failed: {e}; using raw HTML")
        return html


# =============================================================================
# Stage 3 — Markdown post-clean (regex cosmetic)
# =============================================================================
# Heading anchor markers with title attribute (Docusaurus + Sphinx variants):
#   [¶](url "Link to this heading")
#   [​](url "Direct link to Section")  — zero-width-space link
#   [#](url "Permalink to ...")
_HEADING_ANCHOR_TITLED_RE = re.compile(
    r'\[[¶#​\s]*\]\([^)]+?\s+"(?:Direct link to|Link to|Permalink|#)[^"]*"\)',
    re.IGNORECASE,
)

# Sphinx pilcrow anchors WITHOUT title attribute: [¶](url)
_PILCROW_ANCHOR_RE = re.compile(r'\[¶\]\([^)]+\)')

# Zero-width-space anchors WITHOUT title attribute: [​](url)
_ZWSP_ANCHOR_RE = re.compile(r'\[​\]\([^)]+\)')

# Empty link text: [](url) or [   ](url)
_EMPTY_LINK_RE = re.compile(r'\[\s*\]\([^)]+\)')

# Logo/icon-wrapping links: [![alt with logo/icon/favicon/brand](src)](href)
_LOGO_LINK_RE = re.compile(
    r'\[\s*!\[[^\]]*?(?:logo|icon|favicon|brand)[^\]]*?\]\([^)]+\)\s*\]\([^)]+\)',
    re.IGNORECASE,
)

# Standalone "Skip to..." / "Theme..." accessibility chrome.
# Allows whitespace inside the link brackets (k3d-style "[ Skip to content ]").
_SKIP_LINE_RE = re.compile(
    r'^\s*\[\s*(?:Skip to (?:main content|content|navigation)|Jump to'
    r'|Theme[\s\w]*|Toggle [\w\s]+)\s*\]\([^)]+\)\s*$',
    re.MULTILINE | re.IGNORECASE,
)

# Source-edit chrome — same allowance for whitespace inside brackets.
_SOURCE_LINK_RE = re.compile(
    r'^\s*\[\s*(?:Show source|View source|Improve this page|Report a bug'
    r'|Edit on GitHub|Edit this page|Open in GitHub)[^\]]*\s*\]\([^)]+\)\s*$',
    re.MULTILINE | re.IGNORECASE,
)

# Sphinx "Previous topic" / "Next topic" / "This page" sections
_PREV_NEXT_BLOCK_RE = re.compile(
    r'^\s*####\s*(?:Previous topic|Next topic|This page)\s*$.*?(?=^\s*(?:#|\Z))',
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)

# Trailing link-list block: 3+ consecutive bullet items where each is
# essentially a single link (with optional trailing description), at end
# of document. Catches:
#   - Docusaurus/Sphinx/MkDocs in-page TOCs (same-page #anchor links)
#   - Cobra/Click CLI auto-generated subcommand lists (cross-page links)
#   - Generic trailing link dumps that survived HTML-level chrome cleanup
# Trade-off: may strip legitimate "Resources" / "Further reading" sections
# at the end of tutorial pages. Conservative bias: stripping a real
# external-references list is recoverable (user can re-add via curation),
# leaving auto-generated chrome in is not (pollutes embeddings + clusters).
_TRAILING_LINK_LIST_RE = re.compile(
    r'(?:^[ \t]*\*[ \t]+\[[^\]]+\]\([^)]+\)[^\n]*\n?){3,}\Z',
    re.MULTILINE,
)

# Cobra/Click CLI auto-generated section: "### SEE ALSO" / "## Related
# Commands" / "### Subcommands" at the end of every CLI command reference
# page. Matches the heading + body until the next heading or end-of-doc.
_SEE_ALSO_BLOCK_RE = re.compile(
    r'^#{1,4}\s*(?:SEE ALSO|See Also|Related Commands?|Subcommands?)\s*$'
    r'.*?(?=^#{1,6}\s|\Z)',
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
# Orphaned "On this page" / "Contents" header preceding a stripped TOC
_ONPAGE_HEADER_RE = re.compile(
    r'\n\s*(?:#{1,4}\s+)?(?:On this page|Contents|Table of contents|In this article)\s*\n*\Z',
    re.IGNORECASE,
)

# Collapse 3+ blank lines → 2
_MULTI_BLANK_RE = re.compile(r'\n{3,}')


def _clean_markdown_chrome(md: str) -> str:
    """Strip leftover chrome patterns that survived Stage 1's HTML pre-clean.

    These patterns appear inside heading text, as standalone lines, or as
    trailing TOC blocks. They can't always be removed at HTML level either
    because they're inside main content OR because the docs platform uses
    container class names we don't recognize.
    """
    md = _HEADING_ANCHOR_TITLED_RE.sub('', md)
    md = _PILCROW_ANCHOR_RE.sub('', md)
    md = _ZWSP_ANCHOR_RE.sub('', md)
    # Logo first (matches the wrapping link), then empty-text remnants
    md = _LOGO_LINK_RE.sub('', md)
    md = _EMPTY_LINK_RE.sub('', md)
    md = _SKIP_LINE_RE.sub('', md)
    md = _PREV_NEXT_BLOCK_RE.sub('', md)
    md = _SOURCE_LINK_RE.sub('', md)
    # Cobra/Click CLI auto-generated SEE ALSO blocks — strip before the
    # generic trailing-link-list since the SEE ALSO heading itself
    # wouldn't match the bullet pattern.
    md = _SEE_ALSO_BLOCK_RE.sub('', md)
    # Generic trailing link-list block — Docusaurus/Sphinx/MkDocs in-page
    # TOCs, Cobra subcommand lists, generic trailing nav. Strip BEFORE
    # collapsing blank lines so the orphan-header strip aligns to \Z.
    md = _TRAILING_LINK_LIST_RE.sub('', md).rstrip()
    md = _ONPAGE_HEADER_RE.sub('', md).rstrip()
    md = _MULTI_BLANK_RE.sub('\n\n', md)
    return md.strip()


# =============================================================================
# Stage 4 — Quality re-gate
# =============================================================================
_LINK_TEXT_RE = re.compile(r'\[([^\]]+)\]\([^)]+\)')
_MIN_CONTENT_CHARS = 200
_MAX_LINK_RATIO = 0.7


def _is_mostly_chrome(md: str) -> bool:
    """Detect pages that are mostly chrome AFTER cleanup.

    Two signals:
      - too short: < _MIN_CONTENT_CHARS (200 chars) → just title + scraps
      - link-dense: > _MAX_LINK_RATIO (70%) of chars are inside [] of links →
        navigation index page, not real content

    Returns True (mostly chrome) → caller skips this page.
    """
    if not md or len(md) < _MIN_CONTENT_CHARS:
        return True
    in_link_chars = sum(len(m) for m in _LINK_TEXT_RE.findall(md))
    if (in_link_chars / len(md)) > _MAX_LINK_RATIO:
        return True
    return False


# =============================================================================
# Public entry point
# =============================================================================
def html_to_markdown(html: str, url: str) -> Optional[str]:
    """
    Convert HTML to clean LLM-ready markdown via the four-stage pipeline.

    Returns None on empty input, conversion failure, or chrome-only page —
    caller should treat None as a soft-fail (log + skip).
    """
    if not html or not html.strip():
        return None
    try:
        _ensure_initialized()
    except Exception as e:
        logger.warning(f"[md-gen] init failed: {e}")
        return None

    # Stage 1 — strip HTML chrome
    cleaned_html = _strip_chrome_html(html)

    # Stage 2 — HTML → markdown (Crawl4AI, no pruner)
    try:
        result = _MD_GEN.generate_markdown(cleaned_html, base_url = url)
    except Exception as e:
        logger.warning(f"[md-gen] convert failed for {url}: {e}")
        return None
    md = getattr(result, "raw_markdown", None)
    if not md or not md.strip():
        return None

    # Stage 3 — regex cosmetic cleanup
    md = _clean_markdown_chrome(md)

    # Stage 4 — quality re-gate
    if _is_mostly_chrome(md):
        logger.info(f"[md-gen] {url}: mostly chrome after cleanup, skipping")
        return None

    return md
