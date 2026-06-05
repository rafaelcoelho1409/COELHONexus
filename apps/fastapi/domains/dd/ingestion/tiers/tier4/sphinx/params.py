from __future__ import annotations

from ..params import USER_AGENT


# --------------------------------------------------------------------------- #
# Shared with the rest of tier4
# --------------------------------------------------------------------------- #
TIMEOUT_S    = 30.0
SPHINX_USER_AGENT = USER_AGENT


# --------------------------------------------------------------------------- #
# inventory.py — objects.inv parser
# --------------------------------------------------------------------------- #
# `std:doc` = Sphinx document (toctree-reachable); `std:label` = named cross-
# reference target (often points at section anchors on narrative pages).
DOC_ROLES: frozenset[str] = frozenset({"std:doc", "std:label"})

# Roles considered "splittable top-level entities" — each becomes one virtual
# sub-page when its page is fetched.
SPLIT_TOP_ROLES: frozenset[str] = frozenset({
    "py:class", "py:exception", "py:function", "py:module",
    "cpp:class", "cpp:function", "cpp:struct",
    "js:class", "js:function",
})

# Member-level roles for the "1 huge class with N methods" fallback — only
# used when a page has <4 top-level entities but ≥4 members.
SPLIT_MEMBER_ROLES: frozenset[str] = frozenset({
    "py:method", "py:attribute", "py:classmethod",
    "py:staticmethod", "py:property", "py:data",
    "cpp:function", "cpp:member",
    "js:function", "js:attribute",
})

# Stable v2 inventory header marker + header line count.
V2_HEADER:     bytes = b"# Sphinx inventory version 2"
HEADER_LINES:  int   = 4


# --------------------------------------------------------------------------- #
# nav.py — DOM-based toctree discovery
# --------------------------------------------------------------------------- #
NAV_CONCURRENCY = 10

# Theme-ordered sidebar selectors. First non-empty match wins so we don't leak
# body links via the generic `a.reference.internal` catch-all.
SIDEBAR_SELECTORS: tuple[str, ...] = (
    "nav.wy-nav-side .wy-menu-vertical a.reference.internal",  # sphinx_rtd_theme
    "nav.bd-docs-nav a.reference.internal",                    # pydata-sphinx-theme
    "div.bd-sidebar a.reference.internal",                     # pydata variant
    "nav.sidebar-tree a.reference.internal",                   # Furo
    "div.sphinxsidebar a.reference.internal",                  # alabaster / classic
    "nav.bd-links a.reference.internal",                       # Book theme legacy
    "nav.md-nav a.md-nav__link",                               # MkDocs Material
)

# Article-body root candidates. Scoping body selectors to one of these excludes
# header / footer / sidebar chrome.
ARTICLE_ROOTS: tuple[str, ...] = (
    "article.bd-article",     # pydata-sphinx-theme
    "article[role='main']",   # Furo, modern themes
    "div[role='main']",       # sphinx_rtd_theme
    "main",                   # MkDocs Material, generic
    "div.document",           # alabaster
)

# Body-scoped selectors — UNION (not first-wins): a page can have both an
# in-body toctree AND sphinx-design cards.
BODY_SELECTORS: tuple[str, ...] = (
    "div.toctree-wrapper a.reference.internal",  # in-body toctree
    "a.sd-stretched-link",                       # sphinx-design cards
    "a.reference.internal",                      # inline :doc: refs
)

SKIP_HREF_PREFIXES: tuple[str, ...] = ("#", "javascript:", "mailto:", "tel:", "data:")


# --------------------------------------------------------------------------- #
# page_split.py — autodoc / anchor splitting thresholds
# --------------------------------------------------------------------------- #
# Minimum anchored H2 count to trigger anchor-split. 12 leaves typical tutorials
# (5-10 H2) alone but catches ADTK demo.html (33 H2s) and Optuna faq.html (24).
ANCHOR_MIN_H2 = 12

# Minimum py-class/py-function blocks to trigger autodoc-split. PyG utils.html
# has dozens; <4 blocks doesn't need fragmenting.
AUTODOC_MIN_BLOCKS = 4

# Per-section minimum size after markdown conversion. Matches Tier 4's
# `MIN_OK_BYTES = 200`.
MIN_BODY_BYTES = 200

# Per-sub-page byte floor specifically for INVENTORY-driven splits. Higher than
# `MIN_BODY_BYTES` because objects.inv splits run on canonical Sphinx pages that
# may carry HUNDREDS of trivial 1-line symbol entries (CPython's
# library/threading.html lists ~25 py:exception sub-classes each ~300B; curses
# lists hundreds of single-function entries at 295-400B). Raising to ~800B
# keeps only sub-pages with enough context to be useful corpus chunks.
INVENTORY_MIN_BODY_BYTES = 800

# After the per-sub-page filter, if fewer than this many useful sub-pages
# survive, abandon the split — a page that yields only 1-2 substantive symbols
# is more coherent whole than as 1-2 fragments + 30 dropped stubs.
INVENTORY_MIN_SPLITS = 4

# Sphinx autodoc declaration containers across Sphinx generations + languages.
# `dl.py.class` is modern (Sphinx 2+ `domain.objtype` convention); `dl.class` is
# the pre-namespaced form still emitted by older RTD projects (ADTK 0.6.2's
# api/detectors.html uses 15 `dl.class` blocks + 117 `dl.method` — the modern
# selector misses these entirely). Splitting at class/function/module level is
# deliberate: nested `dl.py.method` blocks stay attached to their parent class
# for context (the inner-member fallback handles 1-class-N-methods pages).
AUTODOC_SELECTOR = (
    # Modern Sphinx 2+ (.py / .cpp / .js prefixed)
    "dl.py.class, dl.py.function, dl.py.exception, dl.py.module, "
    "dl.py.data, dl.py.attribute, dl.py.classmethod, dl.py.staticmethod, "
    "dl.cpp.class, dl.cpp.function, dl.cpp.struct, "
    "dl.js.function, dl.js.class, "
    # Older Sphinx + nbsphinx (pre-namespaced)
    "dl.class, dl.function, dl.exception, dl.data"
)
