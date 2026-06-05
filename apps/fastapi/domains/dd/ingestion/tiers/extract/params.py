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
)

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
