"""`_Shell(active_key, ...)` — page chrome shared by every feature page.

Layout (inside `.topbar-wrap`, grid row 1 of `.shell`):
  row 1  brand + global nav pills + settings gear         (always)
  row 2  feature title (red bar) + stage tabs / actions   (when set)
  row 3  contextual toolbar                               (when set)

Body sits in `.page > .card > main#content` (grid row 2 — only this region
scrolls). Skip-link is the first focusable element. Topbar rows carry
`.topbar-collapsible` so topbar.js can auto-hide them on scroll-down."""
from fasthtml.common import (
    H1, A, Div, Main, Nav, Span, Title,
)

from .icons import _GEAR_SVG
from .urls import FEATURES


def _Shell(active_key: str, title_text=None, body=None, title_actions=None,
           subnav_row=None, toolbar_row=None):
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
    # Row 2 derivation — explicit title_text wins (YouTube); else, when stage
    # sub-nav is present (Docs Distiller), use the FEATURES label so the name
    # shows beside the tabs. Home passes neither → no row 2.
    if title_text is None and subnav_row is not None:
        title_text = next(
            (label for key, label, _ in FEATURES if key == active_key), None)
    feature_row = (
        Div(
            (H1(title_text, cls = "title") if title_text else ""),
            (subnav_row if subnav_row is not None
             else (title_actions if title_actions is not None else "")),
            cls = "feature-row topbar-collapsible")
        if (title_text or subnav_row is not None) else ""
    )
    return (
        Title("COELHO Nexus"),
        A("Skip to content", href = "#content", cls = "skip-link"),
        Div(
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
                    A(
                        _GEAR_SVG,
                        href = "/settings",
                        cls = ("settings-gear active" if active_key == "settings"
                               else "settings-gear"),
                        aria_label = "Settings",
                        title = "Settings",
                    ),
                    cls = "topbar",
                ),
                feature_row,
                (toolbar_row if toolbar_row is not None else ""),
                cls = "topbar-wrap",
            ),
            Div(
                Div(
                    Main(
                        body if body is not None else "",
                        id = "content",
                        cls = "panel",
                    ),
                    cls = "card",
                ),
                cls = "page",
            ),
            cls = "shell",
        ),
    )
