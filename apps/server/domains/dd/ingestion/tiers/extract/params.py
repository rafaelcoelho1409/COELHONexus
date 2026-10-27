"""Chrome + content-root selector lists for the HTML→MD pipeline."""
from __future__ import annotations


# Tags to strip outright before conversion. Covers the usual chrome that
# would otherwise leak into the markdown body: navs, sidebars, footers,
# noscript fallbacks, scripts, styles, forms, and SVG icons.
CHROME_SELECTORS: tuple[str, ...] = (
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
    # What the sites in this tier were seen to leave in the page (2026-10-27, Tier 3 over seven docs):
    # buttons ("Back to top", "Copy page"), the host's own "not for print" marker, text only a screen reader reads (a
    # heading's "Section titled “Overview”"), Docusaurus' collapsed mobile "On this page", mkdocs-material's back-to-top
    # button, Grafana's breadcrumb and its feedback + related-articles footer, Mintlify's feedback bar and "Connect these
    # docs" box.
    "button",
    ".print-hidden", ".hidden-print", ".d-print-none", ".no-print", ".noprint",
    ".sr-only", ".visually-hidden", ".visuallyhidden", ".screen-reader-text", ".sl-anchor-link",
    ".theme-doc-toc-mobile", '[class*="tocCollapsible"]',
    ".md-top", ".md-nav", ".md-header", ".md-footer",
    ".docs__breadcrumb", ".docs__feedback",
    ".feedback-toolbar", ".source-links", "#page-context-menu",
)

# An `<aside>` that is a callout (Starlight's Note / Tip / Caution), not page furniture: kept, as a block quote. The
# tag is in CHROME_SELECTORS because most asides are sidebars and tables of contents; these are told apart by their
# class (or role), and one that also looks like a sidebar or a table of contents stays chrome.
CALLOUT_ASIDE_MARKERS: tuple[str, ...] = ("starlight-aside", "admonition", "callout", "alert", "note", "tip", "warning", "caution")
NOT_CALLOUT_MARKERS: tuple[str, ...] = ("sidebar", "toc", "contents", "on this page", "nav", "menu", "related", "footnote")

# What separates a page's name from its site's in a `<title>` ("Quick start | Grafana Tempo documentation").
TITLE_SEPARATORS: tuple[str, ...] = (" | ", " - ", " – ", " — ", " · ", " :: ", " « ", " » ")

# Common "main content" candidates — try these first before falling back to
# <body>. Many docs sites (Sphinx, mkdocs, Docusaurus, Nextra, GitBook,
# Mintlify) use one of these patterns.
CONTENT_SELECTORS: tuple[str, ...] = (
    "main",
    "article",
    '[role="main"]',
    "#content", "#main", "#main-content", "#docs-content",
    ".content", ".main", ".main-content", ".docs-content",
    ".markdown-body", ".prose",
    ".article", ".post", ".doc-content",
)
