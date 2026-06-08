"""Ingest · Library — sidebar facets + row-card list of ingested videos.

Replaces the legacy `_LibrarySection` Channels + Playlists grids with a
single source-of-truth flat list driven by `/api/v1/ycs/admin/videos`.
Inspired by the 2026 SOTA patterns surveyed (Linear, Notion, Vercel
deploy lists, YouTube Studio): a 240px sidebar with checkable filter
facets on the left, a card-row list on the right, a bulk-action
floating bar that appears on multi-select.

The DOM here is structural only — `static/js/ycs/ingest/library.js`
fetches data, renders rows, and binds the per-row trash button + bulk
floating bar. Server-side rendering of rows would mean a round-trip
on every checkbox click; the SPA-ish split keeps it snappy."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, H2, Input, Span


def _FilterGroup(group_id: str, title: str):
    """One sidebar facet group — title + list container. Items get
    pushed in by JS once `/admin/videos/facets` returns. Each item
    becomes a labeled checkbox with a trailing count chip."""
    return Div(
        Div(title, cls = "ycs-lib-facet-title"),
        Div(
            "",  # JS populates
            id  = f"ycs-lib-facet-{group_id}",
            cls = "ycs-lib-facet-list",
            data_group = group_id,
        ),
        cls = "ycs-lib-facet-group",
    )


def _Sidebar():
    """Left rail — facet groups + a clear-filters reset button. Width
    fixed via CSS so the row list stays a predictable width."""
    return Div(
        Div(
            Span("Filters", cls = "ycs-lib-sidebar-title"),
            Button(
                "Clear",
                type  = "button",
                id    = "ycs-lib-clear-filters",
                cls   = "ycs-lib-clear-btn",
                title = "Reset all selected facets",
            ),
            cls = "ycs-lib-sidebar-head",
        ),
        _FilterGroup("status",    "Status"),
        _FilterGroup("channels",  "Channels"),
        _FilterGroup("languages", "Languages"),
        id  = "ycs-lib-sidebar",
        cls = "ycs-lib-sidebar",
    )


def _Toolbar():
    """Top toolbar — count + search input + pagination control."""
    return Div(
        Div(
            Span("Library · ", cls = "ycs-lib-toolbar-label"),
            Span("0", id = "ycs-lib-count", cls = "ycs-lib-count"),
            Span(" videos", cls = "ycs-lib-toolbar-label"),
            cls = "ycs-lib-toolbar-head",
        ),
        Form(
            Input(
                type        = "search",
                name        = "q",
                id          = "ycs-lib-search",
                placeholder = "Search title / channel / description…",
                cls         = "ycs-lib-search",
                autocomplete = "off",
            ),
            cls = "ycs-lib-search-form",
            onsubmit = "return false;",
        ),
        cls = "ycs-lib-toolbar",
    )


def _BulkBar():
    """Floating action bar (June 2026 SOTA Gmail / Linear idiom) that
    appears at the bottom of the list when at least one row is
    selected. Each action POSTs the selected ids to its backend
    endpoint. Hidden by default; JS adds `.visible` on selection."""
    return Div(
        Span("0", id = "ycs-lib-bulk-count", cls = "ycs-lib-bulk-count"),
        Span(" selected · ", cls = "ycs-lib-bulk-sep"),
        Button(
            "Re-ingest",
            type  = "button",
            id    = "ycs-lib-bulk-reingest",
            cls   = "ycs-lib-bulk-btn",
            title = (
                "Dispatch a new pipeline chain (extract → Qdrant → "
                "Neo4j → invalidate) for the selected videos. "
                "Phase 1 hits the cache if transcripts are already "
                "in ES — use `Delete` first if you need a true fresh "
                "ingest."
            ),
        ),
        Button(
            "Delete",
            type  = "button",
            id    = "ycs-lib-bulk-delete",
            cls   = "ycs-lib-bulk-btn ycs-lib-bulk-btn-danger",
            title = (
                "Wipe ES metadata + transcripts, Qdrant points, and "
                "Neo4j Document + Video nodes for the selected "
                "videos. Entity nodes are left intact. This cannot "
                "be undone."
            ),
        ),
        Button(
            "✕",
            type       = "button",
            id         = "ycs-lib-bulk-cancel",
            cls        = "ycs-lib-bulk-cancel",
            title      = "Clear selection",
            aria_label = "Clear selection",
        ),
        id  = "ycs-lib-bulk-bar",
        cls = "ycs-lib-bulk-bar",
    )


def LibraryPanel():
    """Top-level Library component: sidebar + main column + floating
    bulk bar. The main column hosts the toolbar and the list of
    library rows (server-rendered placeholder, JS replaces).

    `id="ycs-lib-panel"` is the boot guard `library.js` looks for —
    without it, the library JS no-ops and the list stays stuck on
    "Loading library…"."""
    return Div(
        _Sidebar(),
        Div(
            _Toolbar(),
            Div(
                Div(
                    "Loading library…",
                    cls = "ycs-lib-empty",
                ),
                id  = "ycs-lib-list",
                cls = "ycs-lib-list",
            ),
            cls = "ycs-lib-main",
        ),
        _BulkBar(),
        id  = "ycs-lib-panel",
        cls = "ycs-lib-panel",
    )
