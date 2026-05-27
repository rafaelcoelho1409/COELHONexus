"""Page chrome shared by every feature.

`HEAD` carries the global CSS + the marked.js CDN; both are referenced
from `static/` instead of being embedded as Python strings so the browser
can cache them and IDE tooling treats them as real files.

`_Shell(active_key, title_text, body=None)` wraps any feature body in the
COELHO-Nexus topbar (brand + nav + title) so individual feature modules
stay focused on their own content.

2026-05-26 (DD-NAVBAR-SOTA Wave A+B):
  - Topbar now uses filled-pill active state + View Transitions pill
    morph between routes (zero JS cost, gracefully degrades).
  - <nav aria-label="Primary"> + a skip-to-content link as the first
    focusable element on every page (a11y baseline).
  - Each nav-item carries a hidden .nav-status-dot. topbar.js polls
    /api/v1/docs-distiller/runs/active and toggles .has-running on the
    matching item — surfaces running ingestion runs at-a-glance.

2026-05-27 (DD-NAVBAR-SOTA Wave C):
  - Topbar is now a full-bleed sticky bar OUTSIDE `.card` so it pins
    to the viewport top (no longer indented by card padding). Pairs
    with the auto-hide-on-scroll-down behavior in topbar.js: pinned
    on load, slides up when scrolling down past the threshold, slides
    back in on any upward scroll. NN/g pattern — keeps the ~22% nav-
    time saving without the permanent screen-real-estate tax.
  - html { scroll-padding-top } ensures the skip-link target + any
    in-page anchors land BELOW the sticky bar instead of under it.
  - prefers-reduced-motion is respected (no slide animation).
"""
from fasthtml.common import (
    H1, A, Div, Link, Main, Meta, Nav, Script, Span, Style, Title,
)


FEATURES = [
    ("home", "Home", "/"),
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]


HEAD = (
    Meta(charset = "UTF-8"),
    Meta(name = "viewport", content = "width=device-width, initial-scale=1.0"),
    Link(rel = "preconnect", href = "https://fonts.googleapis.com"),
    Link(rel = "preconnect", href = "https://fonts.gstatic.com", crossorigin = ""),
    Link(
        rel = "stylesheet",
        href = (
            "https://fonts.googleapis.com/css2?"
            "family=Raleway:wght@300;400;500;600;700&display=swap"
        ),
    ),
    # Client-side markdown renderer for the file-content drawer +
    # Step 5 Study chapter viewer. Pinned major version — zero deps,
    # ~50 KB gzip over the jsDelivr CDN.
    Script(src = "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
    # Syntax highlighting for code blocks in the Study viewer (Step 5).
    # highlight.js — most-adopted SOTA highlighter as of 2026 (10M wk
    # downloads, zero-config auto-detect, 190+ langs, no dependencies).
    # Theme: GitHub Dark (matches the burgundy + sharp-radius aesthetic).
    Link(
        rel = "stylesheet",
        href = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/github-dark.min.css",
    ),
    Script(
        src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js",
        defer = True,
    ),
    # Cytoscape.js — DAG canvas for per-stage LangGraph visualization
    # (Planner / Synth / Curator / Critic / Assembler). Pinned to a 3.x
    # patch via cdnjs. ~320 KB minified, browser-cached aggressively.
    # See `docs/UI-ARCHITECTURE-SOTA-2026-05-18.md`. The graph view
    # activates only when `?ui=graph` is on the URL; without Cytoscape
    # loaded, the JS falls back cleanly to the legacy cards layout.
    Script(
        src = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js",
        defer = True,
    ),
    # Dagre — hierarchical DAG layout algorithm. Cytoscape's own docs
    # ("Using layouts", 2024) recommend dagre as the first-choice
    # layout for DAGs and trees; `breadthfirst` produces less
    # traditional results for sequential pipelines. ~140 KB total
    # across both scripts, served from unpkg.
    Script(
        src = "https://unpkg.com/dagre@0.8.5/dist/dagre.min.js",
        defer = True,
    ),
    Script(
        src = "https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js",
        defer = True,
    ),
    Link(rel="stylesheet", href="/static/css/base.css"),
    Link(rel="stylesheet", href="/static/css/components.css"),
    Link(rel="stylesheet", href="/static/css/home.css"),
    Link(rel="stylesheet", href="/static/css/dd/picker.css"),
    Link(rel="stylesheet", href="/static/css/dd/ingestion.css"),
    Link(rel="stylesheet", href="/static/css/dd/planner.css"),
    Link(rel="stylesheet", href="/static/css/dd/study.css"),
    Link(rel="stylesheet", href="/static/css/youtube.css"),
    # DD-NAVBAR-SOTA-2026-05-26 (Wave B1) — running-work status dot
    # polling. Hits /api/v1/docs-distiller/runs/active every 30s,
    # toggles .has-running on the matching nav-item. defer so it
    # doesn't block first paint.
    Script(src = "/static/js/topbar.js", defer = True),
)


def _Shell(active_key: str, title_text=None, body=None):
    """Page chrome. Pass `title_text=None` to skip the burgundy-bordered
    title row (used by the home page which provides its own hero)."""
    # Wave B1: each nav-item carries a hidden .nav-status-dot. topbar.js
    # toggles .has-running on items whose `data-status-slug` is currently
    # tracked as in-flight by the backend.
    nav_links = [
        A(
            label,
            Span(cls = "nav-status-dot", aria_hidden = "true"),
            href = href,
            cls = "nav-item active" if key == active_key else "nav-item",
            data_status_key = key,
        )
        for key, label, href in FEATURES
    ]
    title_row = (
        Div(
            H1(title_text, cls = "title"), cls = "title-row")
        if title_text else ""
    )
    return (
        Title("COELHO Nexus"),
        # Wave B3 — skip-link is the first focusable element. Targets
        # the <main id="content"> wrapper below. Hidden until focused
        # (see .skip-link in base.css).
        A("Skip to content", href = "#content", cls = "skip-link"),
        # Wave C — sticky topbar lives OUTSIDE the card so it can pin
        # to the viewport edge with a full-width background. The wrap
        # carries `position: sticky` + stuck/hidden state classes (set
        # by topbar.js); the inner `.topbar` keeps the brand+nav inset
        # aligned with the card's horizontal padding.
        Div(
            Div(
                A(
                    Span(cls = "brand-flag"),
                    Span("COELHO Nexus"),
                    href = "/",
                    cls = "brand",
                    aria_label = "COELHO Nexus home",
                ),
                Nav(*nav_links, cls = "nav", aria_label = "Primary"),
                cls = "topbar",
            ),
            cls = "topbar-wrap",
        ),
        Div(
            Div(
                title_row,
                # Wave B3 — wrap the feature body in <main id="content">
                # so the skip-link and screen readers have a landmark.
                Main(
                    body if body is not None else "",
                    id = "content",
                    cls = "panel",
                ),
                cls = "card",
            ),
            cls = "page",
        ),
    )
