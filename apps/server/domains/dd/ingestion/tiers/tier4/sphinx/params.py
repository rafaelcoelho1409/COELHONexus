from __future__ import annotations
# Module-level constant below needs the parent tier4 params RESOLVED at
# this file's own import time — the global `domains.dd...` chase can't
# be used here (docs/CODE-CONVENTIONS.md §8 Exception 2). A sibling-
# rooted `from .. import params` is always safe at module level since
# that import already blocked until tier4/params.py was fully ready.
from .. import params as _tier4_params



TIMEOUT_S    = 30.0
SPHINX_USER_AGENT = _tier4_params.USER_AGENT


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


# Landing sidebar ≥ this many links → assume full tree rendered (skip expansion BFS).
FULL_TREE_HINT = 25

# The same selector for the members of a class (used when a page has fewer than AUTODOC_MIN_BLOCKS top-level entities).
MEMBER_SELECTOR = (
    "dl.py.method, dl.py.attribute, dl.py.classmethod, "
    "dl.py.staticmethod, dl.py.property, dl.py.data, "
    "dl.cpp.function, dl.cpp.member, dl.js.function, dl.js.attribute, "
    # Older Sphinx pre-namespaced forms
    "dl.method, dl.attribute, dl.classmethod, dl.staticmethod"
)

# A piece's slug suffix is its anchor, cut at this length; a piece with none is called `section`. A narrative piece whose anchor is
# taken gets this appended (and a counter after it).
SLUG_MAX_CHARS = 80
SLUG_FALLBACK = "section"
NARRATIVE_SUFFIX = "-narrative"

# A piece's heading is cut at this length (it becomes the end of its title); the text of a `<dt>` or `<h2>` used as a fallback
# title, at this one. `¶` is Sphinx's permalink mark after a heading.
HEADING_MAX_CHARS = 120
TITLE_TEXT_MAX_CHARS = 160
PERMALINK_MARK = "¶"

# `objects.inv` is looked for in the docs folder; with no release folder in the path, in these siblings of it too.
INVENTORY_FILENAME = "objects.inv"
INVENTORY_SIBLINGS = ("stable/", "latest/", "main/")
INVENTORY_PROJECT_PREFIX = "# Project:"
INVENTORY_VERSION_PREFIX = "# Version:"

# The sidebar and body links of a section page are read to this depth, for at most this many pages.
TOCTREE_MAX_DEPTH = 1
TOCTREE_MAX_PAGES = 50
