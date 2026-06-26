from __future__ import annotations

from ..params import USER_AGENT


TIMEOUT_S    = 30.0
SPHINX_USER_AGENT = USER_AGENT


# `std:doc` = toctree-reachable document; `std:label` = section-anchor cross-reference
DOC_ROLES: frozenset[str] = frozenset({"std:doc", "std:label"})

SPLIT_TOP_ROLES: frozenset[str] = frozenset({
    "py:class", "py:exception", "py:function", "py:module",
    "cpp:class", "cpp:function", "cpp:struct",
    "js:class", "js:function",
})

# member-level fallback: used when a page has <4 top-level entities but ≥4 members
SPLIT_MEMBER_ROLES: frozenset[str] = frozenset({
    "py:method", "py:attribute", "py:classmethod",
    "py:staticmethod", "py:property", "py:data",
    "cpp:function", "cpp:member",
    "js:function", "js:attribute",
})

V2_HEADER:     bytes = b"# Sphinx inventory version 2"
HEADER_LINES:  int   = 4


NAV_CONCURRENCY = 10

# theme-ordered: first non-empty match wins; avoids leaking body links via generic catch-all
SIDEBAR_SELECTORS: tuple[str, ...] = (
    "nav.wy-nav-side .wy-menu-vertical a.reference.internal",  # sphinx_rtd_theme
    "nav.bd-docs-nav a.reference.internal",                    # pydata-sphinx-theme
    "div.bd-sidebar a.reference.internal",                     # pydata variant
    "nav.sidebar-tree a.reference.internal",                   # Furo
    "div.sphinxsidebar a.reference.internal",                  # alabaster / classic
    "nav.bd-links a.reference.internal",                       # Book theme legacy
    "nav.md-nav a.md-nav__link",                               # MkDocs Material
)

# scoping to these roots excludes header / footer / sidebar chrome
ARTICLE_ROOTS: tuple[str, ...] = (
    "article.bd-article",     # pydata-sphinx-theme
    "article[role='main']",   # Furo, modern themes
    "div[role='main']",       # sphinx_rtd_theme
    "main",                   # MkDocs Material, generic
    "div.document",           # alabaster
)

# UNION (not first-wins): a page can have both in-body toctree AND sphinx-design cards
BODY_SELECTORS: tuple[str, ...] = (
    "div.toctree-wrapper a.reference.internal",  # in-body toctree
    "a.sd-stretched-link",                       # sphinx-design cards
    "a.reference.internal",                      # inline :doc: refs
)

SKIP_HREF_PREFIXES: tuple[str, ...] = ("#", "javascript:", "mailto:", "tel:", "data:")


# 12 leaves typical tutorials (5-10 H2) alone but catches pages with 20+ H2s
ANCHOR_MIN_H2 = 12

AUTODOC_MIN_BLOCKS = 4

MIN_BODY_BYTES = 200

# higher than MIN_OK_BYTES: inventory splits can yield hundreds of trivial 1-line stubs at ~300B each
INVENTORY_MIN_BODY_BYTES = 800

# if fewer than this many substantive sub-pages survive, the whole is more coherent than fragments
INVENTORY_MIN_SPLITS = 4

# `dl.class` is the pre-namespaced form still emitted by older RTD projects; modern selector misses it
AUTODOC_SELECTOR = (
    # Modern Sphinx 2+ (.py / .cpp / .js prefixed)
    "dl.py.class, dl.py.function, dl.py.exception, dl.py.module, "
    "dl.py.data, dl.py.attribute, dl.py.classmethod, dl.py.staticmethod, "
    "dl.cpp.class, dl.cpp.function, dl.cpp.struct, "
    "dl.js.function, dl.js.class, "
    # Older Sphinx + nbsphinx (pre-namespaced)
    "dl.class, dl.function, dl.exception, dl.data"
)
