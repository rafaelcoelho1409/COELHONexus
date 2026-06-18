"""Row 3 — contextual stage tools for the YCS wizard.

Mirrors `features/dd/shared/toolbar.py`: a `.dd-toolbar` (shared CSS
with Docs Distiller) carrying per-stage chrome on the left. The
right side will hold the library-picker dropdown once Slice 2 of
the YCS port introduces a library identifier (`?slug=`). Until then
the right cluster stays empty.

Returns `None` when the active stage has nothing to put in row 3 —
that lets `routes.py` pass it straight through to `_Shell`, which
renders an empty string when `toolbar_row` is None (= no row 3 at
all, no thin empty bar).

`ingestion` puts the Library's search field + the 3 facet filters
(Status / Channels / Languages) here as popover triggers — same
chrome family as DD's `dd-catfilter`, same event-delegation root
expected by `library.js`. Frees the Library panel of sidebar +
toolbar so the list spans the full Ingestion-page width."""
from __future__ import annotations

from fasthtml.common import Button, Div, Form, Input, Span

from ..ask.chrome    import (
    AskModeTabs,
    AskNewThreadButton,
    AskScopeTrigger,
    AskThreadBar,
)
from ..query.chrome  import QueryBackendTabs
from ..source.chrome import SourceModeTabs


def _FilterTrigger(group: str, title: str):
    """One facet popover for the Library — trigger button + a popover
    that hosts the existing `.ycs-lib-facet-list` container so
    `library.js::renderFacets` keeps targeting the same id without
    code changes. Visual language: DD's `dd-catfilter` (trigger pill,
    chevron, anchored popover) — reused so the two products feel
    like one chrome family."""
    return Div(
        Button(
            Span(f"{title}:",
                 cls = "dd-catfilter-prefix"),
            Span("All",
                 cls = "dd-catfilter-label",
                 id  = f"ycs-lib-filter-{group}-label"),
            Span("▾", cls = "dd-catfilter-chevron"),
            type       = "button",
            cls        = "dd-catfilter-trigger",
            id         = f"ycs-lib-filter-{group}-trigger",
            aria_label = f"Filter by {title.lower()}",
            data_group = group,
        ),
        Div(
            Div(
                "",  # JS populates via `renderFacets`.
                id  = f"ycs-lib-facet-{group}",
                cls = "ycs-lib-facet-list",
                data_group = group,
            ),
            cls = "dd-catfilter-popover",
        ),
        cls = "dd-catfilter ycs-lib-filter",
        data_group = group,
    )


def _LibraryFilters():
    """Row-3 left cluster for `ingestion` — search + 3 facet popovers
    + a Clear button. Same DOM ids the previous sidebar used so
    library.js wires up unchanged."""
    return Div(
        # Library count — surfaces the "N videos" header that used to
        # live in the panel toolbar. Kept here so the panel itself
        # can become a pure list surface.
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
        _FilterTrigger("status",    "Status"),
        _FilterTrigger("channels",  "Channels"),
        _FilterTrigger("languages", "Languages"),
        Button(
            "Clear",
            type  = "button",
            id    = "ycs-lib-clear-filters",
            cls   = "ycs-lib-clear-btn",
            title = "Reset all selected facets",
        ),
        cls = "ycs-lib-filters",
        id  = "ycs-lib-filters",
    )


def StageToolbar(active_stage: str, slug: str | None):
    right: list = []
    if active_stage == "source":
        left = [SourceModeTabs("search")]
    elif active_stage == "ingestion":
        left = [_LibraryFilters()]
    elif active_stage == "ask":
        # Per-request request-shaping on the left (mode + channel
        # scope); session + agent settings on the right (New thread
        # action + Thread picker + LLM info). `+ New thread` is
        # promoted out of the Thread dropdown so it's a one-click
        # primary action, not a hidden secondary one.
        left  = [AskModeTabs(), AskScopeTrigger()]
        right = [AskNewThreadButton(), AskThreadBar()]
    elif active_stage == "query":
        # Backend pill on the left only — Query is scoped to YCS while
        # the cross-app surface is rebuilt. The right cluster (app
        # pivot DD/YCS/RR) was removed 2026-06-15; can come back once
        # DD + RR have something worth browsing here.
        left = [QueryBackendTabs()]
    else:
        return None
    children = [Div(*left, cls = "dd-toolbar-left")]
    if right:
        children.append(Div(*right, cls = "dd-toolbar-right"))
    return Div(
        *children,
        cls = "dd-toolbar topbar-collapsible",
        id = "ycs-toolbar",
    )
