"""Ingest · Library — full-width video list, filters hoisted to row 3.

Replaces the legacy `_LibrarySection` Channels + Playlists grids with a
single source-of-truth flat list driven by `/api/v1/ycs/admin/videos`.

2026-06-10 redesign: the 240px sidebar (3 facet groups) and the inline
panel toolbar (count + search field) were lifted up to the shell's
row-3 toolbar (see `shared/toolbar.py::_LibraryFilters`). The panel
itself is now a pure list surface — a single bordered scroll region
hosting the rows + the floating bulk-action bar. Pattern: GitHub
Issues / Linear — context controls in the header, content takes the
full body width.

The DOM here is structural only — `static/js/ycs/ingest/library.js`
fetches data, renders rows, and binds the per-row trash button + bulk
floating bar. Server-side rendering of rows would mean a round-trip
on every checkbox click; the SPA-ish split keeps it snappy."""
from __future__ import annotations

from fasthtml.common import Button, Div, Span


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
    """Full-width video list + floating bulk-action bar. Filters live
    in the row-3 toolbar (`shared/toolbar.py::_LibraryFilters`).

    `id="ycs-lib-panel"` is the boot guard `library.js` looks for —
    without it, the library JS no-ops and the list stays stuck on
    "Loading library…"."""
    return Div(
        Div(
            Div(
                "Loading library…",
                cls = "ycs-lib-empty",
            ),
            id  = "ycs-lib-list",
            cls = "ycs-lib-list",
        ),
        _BulkBar(),
        id  = "ycs-lib-panel",
        cls = "ycs-lib-panel",
    )
