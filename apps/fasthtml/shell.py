"""Page chrome shared by every feature.

`HEAD` carries the global CSS + the marked.js CDN; both are referenced
from `static/` instead of being embedded as Python strings so the browser
can cache them and IDE tooling treats them as real files.

`_Shell(active_key, title_text, body=None)` wraps any feature body in the
COELHO-Nexus topbar (brand + nav + title) so individual feature modules
stay focused on their own content.
"""
from fasthtml.common import (
    H1, A, Div, Link, Meta, Script, Span, Style, Title,
)


FEATURES = [
    ("home", "Home", "/"),
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]


HEAD = (
    Meta(charset="UTF-8"),
    Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
    Link(rel="preconnect", href="https://fonts.googleapis.com"),
    Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    Link(
        rel="stylesheet",
        href=(
            "https://fonts.googleapis.com/css2?"
            "family=Raleway:wght@300;400;500;600;700&display=swap"
        ),
    ),
    # Client-side markdown renderer for the file-content drawer +
    # Step 5 Study chapter viewer. Pinned major version — zero deps,
    # ~50 KB gzip over the jsDelivr CDN.
    Script(src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
    # Syntax highlighting for code blocks in the Study viewer (Step 5).
    # highlight.js — most-adopted SOTA highlighter as of 2026 (10M wk
    # downloads, zero-config auto-detect, 190+ langs, no dependencies).
    # Theme: GitHub Dark (matches the burgundy + sharp-radius aesthetic).
    Link(
        rel="stylesheet",
        href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/github-dark.min.css",
    ),
    Script(
        src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js",
        defer=True,
    ),
    # Cytoscape.js — DAG canvas for per-stage LangGraph visualization
    # (Planner / Synth / Curator / Critic / Assembler). Pinned to a 3.x
    # patch via cdnjs. ~320 KB minified, browser-cached aggressively.
    # See `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md`. The graph view
    # activates only when `?ui=graph` is on the URL; without Cytoscape
    # loaded, the JS falls back cleanly to the legacy cards layout.
    Script(
        src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js",
        defer=True,
    ),
    # Dagre — hierarchical DAG layout algorithm. Cytoscape's own docs
    # ("Using layouts", 2024) recommend dagre as the first-choice
    # layout for DAGs and trees; `breadthfirst` produces less
    # traditional results for sequential pipelines. ~140 KB total
    # across both scripts, served from unpkg.
    Script(
        src="https://unpkg.com/dagre@0.8.5/dist/dagre.min.js",
        defer=True,
    ),
    Script(
        src="https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js",
        defer=True,
    ),
    Link(rel="stylesheet", href="/static/css/app.css"),
)


def _Shell(active_key: str, title_text=None, body=None):
    """Page chrome. Pass `title_text=None` to skip the burgundy-bordered
    title row (used by the home page which provides its own hero)."""
    nav_links = [
        A(
            label,
            href=href,
            cls="nav-item active" if key == active_key else "nav-item",
        )
        for key, label, href in FEATURES
    ]
    title_row = (
        Div(H1(title_text, cls="title"), cls="title-row")
        if title_text else ""
    )
    return (
        Title("COELHO Nexus"),
        Div(
            Div(
                Div(
                    # Brand is a link to the home page. Same visual as before
                    # — A inherits .brand's flex/colour styles.
                    A(
                        Span(cls="brand-flag"),
                        Span("COELHO Nexus"),
                        href="/", cls="brand",
                    ),
                    Div(*nav_links, cls="nav"),
                    cls="topbar",
                ),
                title_row,
                Div(body if body is not None else "", cls="panel"),
                cls="card",
            ),
            cls="page",
        ),
    )
