"""Header-anchored framework picker — replaces the 260px left rail.

Trigger button shows the current selection ("LangChain ▾") or a placeholder.
Click opens a right-aligned popover with a search input + ingested-framework
list (same id="fw-sidebar-list" the old rail used → library.js renders into
it unchanged). Matches GitHub repo switcher / Vercel project switcher —
established 2026 pattern for a primary resource picker."""
from fasthtml.common import Button, Div, Img, Input, Span

from ..cache import fetch_catalog


def FrameworkPicker(slug: str | None, catalog: list[dict] | None = None):
    # Resolve the current selection's display info server-side so the
    # trigger renders with the right name + logo — no JS flicker.
    # Multi-logo stacks (LangChain bundle, Grafana bundle) render the
    # first logo as the trigger badge.
    info = None
    if slug:
        catalog = catalog or fetch_catalog()
        for f in catalog or []:
            if f.get("slug") == slug:
                info = f
                break
    label = (info or {}).get("name") or slug or "Library"
    logos = (info or {}).get("logos") or []
    primary_logo = logos[0] if logos else (info or {}).get("logo")

    trigger_children = []
    if primary_logo:
        trigger_children.append(
            Img(src = primary_logo, alt = "", cls = "dd-fw-picker-logo"))
    trigger_children.append(Span(label, cls = "dd-fw-picker-label"))
    trigger_children.append(Span("▾", cls = "dd-fw-picker-chevron",
                                 aria_hidden = "true"))

    return Div(
        Button(
            *trigger_children,
            type = "button",
            id = "dd-fw-picker-trigger",
            cls = "dd-fw-picker-trigger",
            aria_haspopup = "listbox",
            aria_expanded = "false",
            aria_label = "Switch ingested framework",
        ),
        Div(
            Input(
                type = "search",
                id = "dd-fw-picker-search",
                placeholder = "Search ingested frameworks…",
                cls = "dd-fw-picker-search",
                autocomplete = "off",
            ),
            # `id="fw-sidebar-list"` is preserved so library.js's
            # renderSidebar() and refresh/delete handlers work unchanged.
            Div(
                Div("Loading…", cls = "fw-sidebar-empty"),
                id = "fw-sidebar-list",
                cls = "dd-fw-picker-list",
            ),
            id = "dd-fw-picker-popover",
            cls = "dd-fw-picker-popover",
            role = "listbox",
        ),
        cls = "dd-fw-picker",
        id = "dd-fw-picker",
        data_dd_slug = (slug or ""),
    )
