"""Catalog toolbar pieces — search input + category dropdown filter.

`CatalogSearch` filters tiles client-side via picker.js. `CategoryFilter`
replaces the old chip row; same open/close + scroll-close behavior as the
framework picker (wired in picker.js). picker.js reads the chosen
`data-chip` into `S.activeChip` and calls applyFilter(). `TierFilter` /
`StatusFilter` are multi-toggle facets (OR within, AND across) wired by the
same module — see picker.js wireFacet()."""
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


def _facet_options(facet: str, options: list[tuple[str, int]]) -> list:
    """One toggle option per facet value + a Reset entry. Counts are
    server-rendered snapshots; picker.js refreshes them live per search."""
    out = [Button(
        Span("Reset", cls = "dd-catfilter-option-label"),
        cls = "dd-catfilter-option dd-catfilter-reset",
        data_facet = facet, data_value = "",
        type = "button", role = "option",
    )]
    for value, count in options:
        out.append(Button(
            Span(value, cls = "dd-catfilter-option-label"),
            Span(str(count), cls = "dd-catfilter-count",
                 id = f"dd-facet-count-{facet}-{value}"),
            cls = "dd-catfilter-option", data_facet = facet,
            data_value = value,
            type = "button", role = "option",
        ))
    return out


def TierFilter(catalog: list[dict] | None):
    """Multi-toggle Tier facet (OR within). Tiers derived server-side
    (`tier` on each catalog entry); unknown tier renders as "—"."""
    catalog = catalog or []
    counts: dict[str, int] = {}
    for f in catalog:
        t = str(f.get("tier") or "—")
        counts[t] = counts.get(t, 0) + 1
    options = _facet_options(
        "tier", sorted(counts.items(), key = lambda kv: kv[0]),
    )
    return Div(
        Button(
            Span("Tier:", cls = "dd-catfilter-prefix"),
            Span("All", id = "dd-tierfilter-label", cls = "dd-catfilter-label"),
            Span("▾", cls = "dd-catfilter-chevron", aria_hidden = "true"),
            id = "dd-tierfilter-trigger", cls = "dd-catfilter-trigger",
            type = "button", aria_haspopup = "listbox", aria_expanded = "false",
            aria_label = "Filter frameworks by ingestion tier",
        ),
        Div(*options, cls = "dd-catfilter-popover", role = "listbox",
            id = "dd-tierfilter-popover"),
        cls = "dd-catfilter", id = "dd-tierfilter",
    )


def StatusFilter():
    """Multi-toggle Status facet (OR within): Ingested vs Not ingested, from
    the client's ingested-slugs set (populated async — counts fill in once
    the library fetch resolves)."""
    options = _facet_options("status", [("Ingested", 0), ("Not ingested", 0)])
    return Div(
        Button(
            Span("Status:", cls = "dd-catfilter-prefix"),
            Span("All", id = "dd-statusfilter-label", cls = "dd-catfilter-label"),
            Span("▾", cls = "dd-catfilter-chevron", aria_hidden = "true"),
            id = "dd-statusfilter-trigger", cls = "dd-catfilter-trigger",
            type = "button", aria_haspopup = "listbox", aria_expanded = "false",
            aria_label = "Filter frameworks by ingestion status",
        ),
        Div(*options, cls = "dd-catfilter-popover", role = "listbox",
            id = "dd-statusfilter-popover"),
        cls = "dd-catfilter", id = "dd-statusfilter",
    )
