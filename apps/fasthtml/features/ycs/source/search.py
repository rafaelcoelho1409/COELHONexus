"""Source · Search mode — sync yt-dlp metadata search (in-page render).

UI shape (June 2026 SOTA — always-visible faceted filter grid):
  - Top row: query input + Search button.
  - Persistent filter grid directly under the input, visible at all
    times. Every filter is a native HTML control (`<select>`,
    `<input type="date|number|text">`, `<input type="checkbox">`) so
    the browser handles all interaction — no JS click handlers to
    break under strict-shield browsers, no popovers to mis-position,
    no portal/stacking-context games.
  - Results controls row (pagination, page-size, density) sits BELOW
    the filter grid so it has its own line and never competes for
    space.
  - Submit-time serialization is unchanged: every visible control has
    a `name=` matching the backend `SearchRequest` field, so the
    existing `FormData`-based `readSearchRequest()` in search.js
    works without any wiring change.

Rationale (per the 2026 e-commerce/SaaS faceted-search research):
"keep frequently-used filters always visible" + "show active filters
prominently as chips with clear remove buttons" + "use native form
controls on desktop". The toggle-dropdown pattern this replaces was
SOTA in 2024 but adds JS-execution risk (Brave Shields, mobile-Brave
desktop-vis quirks, ES-module init races) that the always-visible
pattern eliminates."""
from __future__ import annotations

from fasthtml.common import (
    Button, Details, Div, Form, Input, Label, Option, Select, Span, Summary,
)


def _FilterField(label: str, control, hint: str | None = None):
    """One labelled filter cell in the grid. Stacked vertically: small
    uppercase label on top, native control below. Optional hint is a
    muted single-line caption below the control."""
    children = [
        Label(label, cls = "ycs-filter-field-label"),
        control,
    ]
    if hint:
        children.append(Span(hint, cls = "ycs-filter-field-hint"))
    return Div(*children, cls = "ycs-filter-field")


def _FilterToggle(label: str, name: str, value: str, title: str = ""):
    """Checkbox toggle. When checked, the form posts `name=value`; when
    unchecked, nothing is sent. The backend's `SearchRequest` reads it
    as a boolean (truthy = checked)."""
    return Label(
        Input(type = "checkbox", name = name, value = value,
              cls = "ycs-filter-toggle-cb"),
        Span(label, cls = "ycs-filter-toggle-label"),
        cls = "ycs-filter-toggle",
        title = title or label,
    )


def _FilterGrid():
    """Collapsed-by-default faceted filter grid. CSS grid layout
    (responsive) arranges the 11 fields into rows of 1–4 columns
    depending on viewport width. Every control has a `name=`
    matching the backend `SearchRequest` schema, so the existing
    FormData reader picks them up unchanged.

    The grid is wrapped in a native `<details>` element so the
    browser handles the open/close toggle directly — no JS click
    handler can break the interaction (no extension, shield, or
    cache state can intercept). The `<details>` starts CLOSED
    (no `open` attribute) and is forced shut after every Search
    submit by `search.js`'s submit handler, returning the panel to
    its collapsed initial state per click.

    Empty fields are sent as empty strings; `readSearchRequest()` in
    search.js drops them before posting, so the backend never sees a
    blank filter (no false-narrow on the search index)."""
    grid = Div(
        # ----- Row 1: type + duration + kind ------------------------
        _FilterField("Duration", Select(
            Option("Any duration", value = ""),
            Option("Under 4 minutes",  value = "Under 4 minutes"),
            Option("4 – 20 minutes",   value = "4 - 20 minutes"),
            Option("Over 20 minutes",  value = "Over 20 minutes"),
            name = "duration", id = "ycs-f-duration",
            cls  = "ycs-filter-control",
        )),
        _FilterField("Kind", Select(
            Option("All kinds", value = ""),
            Option("Videos only",    value = "video"),
            Option("Channels only",  value = "channel"),
            Option("Playlists only", value = "playlist"),
            name = "kind_filter", id = "ycs-f-kind_filter",
            cls  = "ycs-filter-control",
        )),
        # ----- Row 2: date range ------------------------------------
        _FilterField("Uploaded after", Input(
            type = "date", name = "date_after", id = "ycs-f-date_after",
            cls  = "ycs-filter-control",
        )),
        _FilterField("Uploaded before", Input(
            type = "date", name = "date_before", id = "ycs-f-date_before",
            cls  = "ycs-filter-control",
        )),
        # ----- Row 3: engagement floors -----------------------------
        _FilterField("Min views", Input(
            type = "number", name = "min_views", id = "ycs-f-min_views",
            placeholder = "e.g. 10000",
            cls  = "ycs-filter-control", min = "0",
        )),
        _FilterField("Max views", Input(
            type = "number", name = "max_views", id = "ycs-f-max_views",
            placeholder = "e.g. 1000000",
            cls  = "ycs-filter-control", min = "0",
        )),
        _FilterField("Min likes", Input(
            type = "number", name = "min_likes", id = "ycs-f-min_likes",
            placeholder = "e.g. 100",
            cls  = "ycs-filter-control", min = "0",
        )),
        # ----- Row 4: text contains ---------------------------------
        _FilterField("Title contains", Input(
            type = "text", name = "title_contains", id = "ycs-f-title_contains",
            placeholder = "Plain text or *=op",
            cls  = "ycs-filter-control",
        )),
        _FilterField("Channel name", Input(
            type = "text", name = "channel_name", id = "ycs-f-channel_name",
            placeholder = "Creator or channel",
            cls  = "ycs-filter-control",
        )),
        # ----- Row 5: toggles ---------------------------------------
        Div(
            _FilterToggle(
                "Sort by newest", "sort_by_date", "newest",
                title = "Order results from newest to oldest upload date",
            ),
            _FilterToggle(
                "Exclude shorts", "exclude_shorts", "yes",
                title = (
                    "Skip videos under ~1 minute or with '/shorts/' in the URL"
                ),
            ),
            cls = "ycs-filter-toggle-row",
        ),
        cls = "ycs-filter-grid",
        id  = "ycs-filter-grid",
    )
    return Details(
        Summary(
            Span("Filters", cls = "ycs-filter-summary-label"),
            Span("▾", cls = "ycs-filter-summary-chevron"),
            cls = "ycs-filter-summary",
        ),
        grid,
        cls = "ycs-filter-details",
        id  = "ycs-filter-details",
        # No `open` attribute → starts collapsed.
    )


def _ResultsControls():
    """Below-grid row with inline pagination + page-size + density.
    Same DOM IDs as before so search.js wires up unchanged.

    (Select-all moved into the results list itself as a master-row
    checkbox above the rows — see `renderResults` in search.js — so
    it sits next to the per-row checkboxes where the eye expects it,
    instead of as a remote button in this cluster.)"""
    return Div(
        # Inline pagination — `data-state` is empty | visible | error,
        # driven by search.js. The middle slot doubles as a status line
        # (`Searching…` / `Search failed` / `1–25 of 25+`).
        Div(
            Button("←", type = "button",
                   cls = "ycs-pagination-btn",
                   id = "ycs-pagination-prev",
                   title = "Previous page",
                   disabled = True),
            Div("", cls = "ycs-pagination-range", id = "ycs-pagination-range"),
            Button("→", type = "button",
                   cls = "ycs-pagination-btn",
                   id = "ycs-pagination-next",
                   title = "Next page",
                   disabled = True),
            cls = "ycs-pagination-inline",
            id  = "ycs-pagination",
            data_state = "empty",
        ),
        Select(
            Option("10 per page",  value = "10"),
            Option("25 per page",  value = "25", selected = True),
            Option("50 per page",  value = "50"),
            Option("100 per page", value = "100"),
            Option("200 per page", value = "200"),
            id  = "ycs-page-size",
            cls = "ycs-page-size",
            title = "Results per page (auto re-fetches)",
        ),
        Div(
            Button("Compact",   type = "button",
                   cls = "ycs-density-btn active",
                   data_density = "compact",
                   id = "ycs-density-compact"),
            Button("Comfortable", type = "button",
                   cls = "ycs-density-btn",
                   data_density = "comfortable",
                   id = "ycs-density-comfortable"),
            cls = "ycs-density-toggle",
        ),
        cls = "ycs-results-controls",
    )


def SearchTab():
    """Search mode panel — active by default in SourceBody.

    Sticky wrapper at the top contains: query row → filter grid →
    results-controls row. CSS overrides `.page`'s padding-top when
    Search is active so the wrapper glues flush to the topbar.
    Results scroll beneath."""
    return Div(
        Div(
            Form(
                # Query row — text input + Search button.
                Div(
                    Input(type = "text", name = "query", id = "ycs-search-query",
                          placeholder = "Search YouTube…",
                          cls = "ycs-search-input", required = True,
                          autocomplete = "off"),
                    Button("Search", type = "submit", cls = "btn-primary"),
                    cls = "ycs-search-row",
                ),
                # Always-visible filter grid (replaces the old toggle
                # dropdown — see module docstring).
                _FilterGrid(),
                # Results-controls row.
                _ResultsControls(),
                # max_results is driven by the page-size <select>; kept
                # as a hidden mirror so the existing readSearchRequest()
                # picks it up uniformly with the other fields.
                Input(type = "hidden", name = "max_results", value = "25",
                      id = "ycs-fh-max_results"),
                id = "ycs-search-form",
            ),
            cls = "ycs-search-sticky",
            id  = "ycs-search-sticky",
        ),
        Div(
            Div("Run a search to see videos.", cls = "ycs-search-empty"),
            id = "ycs-search-results",
            cls = "ycs-search-results",
            data_density = "compact",
        ),
        cls = "ycs-tab-body active",
        id  = "ycs-tab-search",
        role = "tabpanel",
    )
