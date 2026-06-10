"""Source · Search mode — sync yt-dlp metadata search (in-page render).

UI shape (June 2026 SOTA, Linear / Vercel / Height filter-bar idiom):
  - Top row: query input + Search button.
  - Single filter-controls row directly under the input. Left half
    holds the filter trigger + active chips; right half clusters the
    result controls (inline Prev/range/Next pagination, page-size
    selector, Compact/Comfortable density toggle). One sticky bundle.
  - Page-size change auto-refetches (no second click on Search needed).
  - The 9 filter fields are hidden `<input>`s — search.js mirrors the
    chip state into them, so FormData serialization at submit-time is
    unchanged."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, Input, Option, Select, Span


_HIDDEN_FILTER_FIELDS = (
    "duration", "date_after", "date_before",
    "min_views", "max_views", "min_likes",
    "title_contains", "channel_name",
    "sort_by_date", "exclude_shorts",
    "kind_filter",
)


def _FilterAddTrigger():
    """`+ Filter` ghost button. Opens the filter-typeahead menu (rendered
    by search.js). Each menu item adds an editable chip + a hidden
    input value."""
    return Button(
        Span("+", cls = "ycs-filter-add-icon"),
        Span("Filter", cls = "ycs-filter-add-label"),
        type = "button",
        cls = "ycs-filter-add",
        id  = "ycs-filter-add-btn",
        aria_haspopup = "true",
        aria_expanded = "false",
    )


def _ResultsControls():
    """Right cluster of the filter row. Contains the inline pagination
    (Prev | range/status | Next), the page-size selector, and the
    Compact/Comfortable density toggle. Same DOM IDs as before so
    search.js wires up unchanged.

    (Select-all moved into the results list itself as a master-row
    checkbox above the rows — see `renderResults` in search.js — so
    it sits next to the per-row checkboxes where the eye expects it,
    instead of as a remote button in this cluster.)"""
    return Div(
        # Inline pagination — `data-state` is empty | visible | error,
        # driven by search.js. The middle slot doubles as a status line
        # (`Searching…` / `Search failed` / `1–25 of 25+`) so there's
        # no separate status element competing for row real estate.
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

    Sticky wrapper at the top contains the search form + filter row
    (chips + result controls) as a single unit. CSS overrides
    `.page`'s padding-top when Search is active so the wrapper glues
    flush to the topbar. Results scroll beneath."""
    return Div(
        Div(
            Form(
                Div(
                    Input(type = "text", name = "query", id = "ycs-search-query",
                          placeholder = "Search YouTube…",
                          cls = "ycs-search-input", required = True,
                          autocomplete = "off"),
                    Button("Search", type = "submit", cls = "btn-primary"),
                    cls = "ycs-search-row",
                ),
                # Filter row — chips on the left, results-controls on
                # the right. Wraps on narrow viewports so the controls
                # drop below the chips instead of clipping.
                Div(
                    Div(_FilterAddTrigger(),
                        cls = "ycs-filter-chips",
                        id  = "ycs-filter-chips"),
                    _ResultsControls(),
                    cls = "ycs-filters-bar",
                ),
                # Hidden filter inputs — chips mirror values here so
                # FormData includes them at submit. max_results is
                # driven by the page-size select.
                *[
                    Input(type = "hidden", name = name, id = f"ycs-fh-{name}")
                    for name in _HIDDEN_FILTER_FIELDS
                ],
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
