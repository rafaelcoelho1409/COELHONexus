"""Ingestion body — progress strip + split-pane docs explorer.

Layout (2026-06-08 redesign, SOTA per Mintlify/Docusaurus/Starlight 2026):

  ┌─ progress strip (hidden until a run is in flight) ─────────────────┐
  ├──────────────────────────────────────────────────────────────────────┤
  │ summary line — N pages · total · tier · age                         │
  ├──────────────┬──────────────────────────────────────────────────────┤
  │ 🔍 search    │ breadcrumb: framework › section › doc-title          │
  │ tier filter  ├──────────────────────────────────────────────────────┤
  │ ▼ section/   │                                                      │
  │   ▸ doc-1    │  rendered markdown preview                           │
  │   ▸ doc-2    │  (marked + DOMPurify + hljs + KaTeX, same pipeline   │
  │ ▼ guides/    │   as the Study chapter view)                         │
  │   ▸ ...      │                                                      │
  └──────────────┴──────────────────────────────────────────────────────┘

The wiring lives in `static/js/dd/ingestion/explorer.js`. The old flat
`#fw-step2-grid` list is gone; click delegation in the shared drawer no
longer fires here (rows carry `.fw-explorer-row`, not `.fw-page-card`),
so the drawer system stays available for Study citation pop-ups without
double-handling.
"""
from fasthtml.common import Button, Div, Input, Span


def _ProgressBox():
    """Live-ingestion progress strip. Hidden by default; pollRun() reveals
    it when a run is in flight. Unchanged DOM contract — selectors used
    by polling.js (#fw-progress-tier, etc.) still resolve."""
    return Div(
        Div(
            Span("—", id = "fw-progress-tier", cls = "fw-progress-tier"),
            Span("idle", id = "fw-progress-status",
                 cls = "fw-progress-status"),
            cls = "fw-progress-head",
        ),
        Div(
            Div(cls = "fw-progress-fill", id = "fw-progress-fill"),
            cls = "fw-progress-bar indeterminate", id = "fw-progress-bar",
        ),
        Div(
            Span("", id = "fw-progress-counter"),
            Span(""),
            cls = "fw-progress-meta",
        ),
        Div("", id = "fw-progress-url", cls = "fw-progress-url"),
        Div(
            Div(
                Div(id = "fw-progress-logos", cls = "fw-progress-logos"),
                Span("", id = "fw-progress-framework",
                     cls = "fw-progress-framework"),
                cls = "fw-progress-framework-box",
            ),
            Button("Cancel ingestion", id = "fw-cancel", cls = "btn-outline"),
            cls = "fw-progress-actions",
        ),
        id = "fw-progress-box", cls = "fw-progress",
        style = "display:none;",
    )


def _ExplorerNav():
    """Left rail: search input → tier-filter chips → grouped tree of
    entries. `explorer.js` populates `#fw-explorer-tree` after the
    manifest arrives."""
    return Div(
        Div(
            Input(
                type = "search",
                id = "fw-explorer-search",
                placeholder = "Search pages…  ( / )",
                autocomplete = "off",
                spellcheck = "false",
                cls = "fw-explorer-search-input",
            ),
            Div(id = "fw-explorer-tier-chips",
                cls = "fw-explorer-tier-chips"),
            cls = "fw-explorer-nav-head",
        ),
        Div(
            Div("Pick a framework to see its files.",
                cls = "fw-empty"),
            id = "fw-explorer-tree",
            cls = "fw-explorer-tree",
        ),
        Div("", id = "fw-explorer-tree-count",
            cls = "fw-explorer-tree-count"),
        cls = "fw-explorer-nav",
        id = "fw-explorer-nav",
    )


def _ExplorerPreview(slug):
    """Right pane: breadcrumb header + scrollable markdown body."""
    return Div(
        Div(
            Span("", id = "fw-explorer-breadcrumb",
                 cls = "fw-explorer-breadcrumb"),
            Div(
                Span("", id = "fw-explorer-meta",
                     cls = "fw-explorer-meta"),
                Button(
                    "↗ Open in drawer",
                    id = "fw-explorer-popout",
                    cls = "btn-outline fw-explorer-popout",
                    title = "Open the selected page in a focused full-"
                            "screen drawer (Esc to close).",
                ),
                cls = "fw-explorer-header-right",
            ),
            cls = "fw-explorer-header",
            id = "fw-explorer-header",
        ),
        Div(
            Div(
                "Pick a framework from the Library dropdown above, or "
                "ingest a new one from the Catalog tab, to browse its "
                "downloaded pages." if not slug else "Loading…",
                cls = "fw-empty",
            ),
            id = "fw-explorer-body",
            cls = "fw-explorer-body",
        ),
        cls = "fw-explorer-preview",
        id = "fw-explorer-preview",
    )


def IngestionBody(slug: str | None):
    # Summary line (`#fw-step2-summary`) lives in the row-3 toolbar
    # (2026-06-08) — the body no longer renders it. The toolbar wires
    # it into `dd-toolbar-left` via `StageToolbar("ingestion", ...)`,
    # so `manifest.js:_renderSummary` keeps targeting the same ID
    # without code changes.
    return Div(
        _ProgressBox(),
        Div(
            _ExplorerNav(),
            _ExplorerPreview(slug),
            cls = "fw-explorer",
            id = "fw-explorer",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-2-panel",
    )
