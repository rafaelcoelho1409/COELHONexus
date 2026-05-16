"""Page chrome shared by every feature.

`HEAD` carries the global CSS + the marked.js CDN; both are referenced
from `static/` instead of being embedded as Python strings so the browser
can cache them and IDE tooling treats them as real files.

`_Shell(active_key, title_text, body=None)` wraps any feature body in the
COELHO-Nexus topbar (brand + nav + title) so individual feature modules
stay focused on their own content.
"""
from fasthtml.common import (
    H1, A, Div, Link, Meta, Script, Span, Style, Title,
)


FEATURES = [
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]


HEAD = (
    Meta(charset="UTF-8"),
    Meta(name="viewport", content="width=device-width, initial-scale=1.0"),
    Link(rel="preconnect", href="https://fonts.googleapis.com"),
    Link(rel="preconnect", href="https://fonts.gstatic.com", crossorigin=""),
    Link(
        rel="stylesheet",
        href=(
            "https://fonts.googleapis.com/css2?"
            "family=Raleway:wght@300;400;500;600;700&display=swap"
        ),
    ),
    # Client-side markdown renderer for the file-content drawer. Pinned
    # major version — zero deps, ~50 KB gzip over the jsDelivr CDN.
    Script(src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
    Link(rel="stylesheet", href="/static/css/app.css"),
)


def _Shell(active_key: str, title_text: str, body=None):
    nav_links = [
        A(
            label,
            href=href,
            cls="nav-item active" if key == active_key else "nav-item",
        )
        for key, label, href in FEATURES
    ]
    return (
        Title("COELHO Nexus"),
        Div(
            Div(
                Div(
                    Div(
                        Span(cls="brand-flag"),
                        Span("COELHO Nexus"),
                        cls="brand",
                    ),
                    Div(*nav_links, cls="nav"),
                    cls="topbar",
                ),
                Div(
                    H1(title_text, cls="title"),
                    cls="title-row",
                ),
                Div(body if body is not None else "", cls="panel"),
                cls="card",
            ),
            cls="page",
        ),
    )
