"""Catalog toolbar pieces — search input + category dropdown filter.

`CatalogSearch` filters tiles client-side via picker.js. `CategoryFilter`
replaces the old chip row; same open/close + scroll-close behavior as the
framework picker (wired in picker.js). picker.js reads the chosen
`data-chip` into `S.activeChip` and calls applyFilter()."""
from fasthtml.common import Button, Div, Input, Span


def CatalogSearch(catalog: list[dict] | None):
    n = len(catalog or [])
    return Div(
        Input(
            type = "search", id = "fw-search",
            placeholder = f"Search {n} frameworks…",
            autocomplete = "off", autofocus = True,
            cls = "fw-search",
        ),
        Span("", id = "fw-count", cls = "fw-count"),
        cls = "fw-search-row",
    )


def CategoryFilter(catalog: list[dict] | None):
    catalog = catalog or []
    counts: dict[str, int] = {}
    for f in catalog:
        c = f.get("category") or "Other"
        counts[c] = counts.get(c, 0) + 1
    cats = sorted(counts)
    options = [
        Button(
            Span("All", cls = "dd-catfilter-option-label"),
            Span(str(len(catalog)), cls = "dd-catfilter-count"),
            cls = "dd-catfilter-option active", data_chip = "All",
            type = "button", role = "option",
        )
    ]
    for c in cats:
        options.append(Button(
            Span(c, cls = "dd-catfilter-option-label"),
            Span(str(counts[c]), cls = "dd-catfilter-count"),
            cls = "dd-catfilter-option", data_chip = c,
            type = "button", role = "option",
        ))
    return Div(
        Button(
            Span("Category:", cls = "dd-catfilter-prefix"),
            Span("All", id = "dd-catfilter-label", cls = "dd-catfilter-label"),
            Span("▾", cls = "dd-catfilter-chevron", aria_hidden = "true"),
            id = "dd-catfilter-trigger", cls = "dd-catfilter-trigger",
            type = "button", aria_haspopup = "listbox", aria_expanded = "false",
            aria_label = "Filter frameworks by category",
        ),
        Div(*options, cls = "dd-catfilter-popover", role = "listbox",
            id = "dd-catfilter-popover"),
        cls = "dd-catfilter", id = "dd-catfilter",
    )
