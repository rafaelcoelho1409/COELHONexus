"""COELHO Nexus — FastHTML base shell.

Visual style derived from the Plotly dash-financial-report sample app:
Raleway typeface, burgundy (#c41230) accent on off-white (#fafafa). Three
feature routes are wired in (Docs Distiller, YouTube Content Search, and a
placeholder slot); each renders the same empty card shell for now.
"""
from fasthtml.common import (
    H1, A, Button, Div, Link, Meta, Span, Style, Title, fast_app, serve,
)
from starlette.responses import PlainTextResponse


# Feature registry — single source of truth for the topbar nav. Rename the
# third entry once we settle on the next feature.
FEATURES = [
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-search", "YouTube Content Search", "/youtube-search"),
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
    Style("""
        :root {
          --bg: #fafafa;
          --card: #ffffff;
          --primary: #c41230;
          --primary-dark: #65201f;
          --text: #2a2a2a;
          --text-muted: #8b8b8b;
          --border: #e5e5e5;
        }
        * { box-sizing: border-box; }
        html, body {
          margin: 0;
          padding: 0;
          background: var(--bg);
          color: var(--text);
          font-family: 'Raleway', 'HelveticaNeue', 'Helvetica Neue',
                       Helvetica, Arial, sans-serif;
          -webkit-font-smoothing: antialiased;
          font-weight: 400;
          line-height: 1.5;
        }
        .page {
          padding: 32px 40px;
        }
        .card {
          background: var(--card);
          width: 100%;
          border: 1px solid var(--border);
          border-radius: 4px;
          padding: 32px 48px 56px 48px;
        }
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 32px;
          margin-bottom: 36px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 14px;
          color: var(--primary);
          font-weight: 600;
          font-size: 1.25rem;
          letter-spacing: 0.01em;
        }
        .brand-flag {
          width: 0;
          height: 0;
          border-style: solid;
          border-width: 0 0 22px 22px;
          border-color: transparent transparent var(--primary) transparent;
          display: inline-block;
        }
        .nav {
          display: flex;
          gap: 6px;
          flex: 1;
          justify-content: center;
        }
        .nav-item {
          padding: 9px 16px;
          font-size: 0.82rem;
          color: var(--text-muted);
          text-decoration: none;
          border-radius: 3px;
          font-weight: 500;
          letter-spacing: 0.02em;
          cursor: pointer;
          transition: color 0.15s, background 0.15s;
        }
        .nav-item:hover { color: var(--text); background: rgba(0,0,0,0.04); }
        .nav-item.active { color: var(--primary); font-weight: 600; }
        .btn-outline {
          background: transparent;
          color: var(--text);
          border: 1px solid var(--border);
          padding: 8px 18px;
          font-size: 0.78rem;
          font-family: inherit;
          border-radius: 3px;
          cursor: pointer;
          font-weight: 500;
          letter-spacing: 0.02em;
          white-space: nowrap;
        }
        .btn-outline:hover { border-color: var(--text-muted); }
        .btn-primary {
          background: var(--primary);
          color: #ffffff;
          border: 0;
          padding: 9px 22px;
          font-size: 0.78rem;
          font-family: inherit;
          border-radius: 3px;
          cursor: pointer;
          font-weight: 600;
          letter-spacing: 0.02em;
          white-space: nowrap;
        }
        .btn-primary:hover { background: var(--primary-dark); }
        .title-row {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 24px;
          padding-left: 16px;
          border-left: 6px solid var(--primary);
          margin-bottom: 32px;
        }
        .title {
          font-size: 1.55rem;
          font-weight: 400;
          color: var(--text);
          line-height: 1.25;
          margin: 0;
        }
        .panel {
          min-height: 360px;
        }
    """),
)


app, rt = fast_app(
    pico=False,
    htmx=False,
    default_hdrs=False,
    live=False,
    hdrs=HEAD,
)


def _Shell(active_key: str, title_text: str):
    """Card shell shared by every feature page: topbar (brand + nav +
    Learn More) -> red-accent title row -> empty content panel."""
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
                    Button("Learn More", cls="btn-outline"),
                    cls="topbar",
                ),
                Div(
                    H1(title_text, cls="title"),
                    Button("Full View", cls="btn-primary"),
                    cls="title-row",
                ),
                Div(cls="panel"),
                cls="card",
            ),
            cls="page",
        ),
    )


@rt("/")
def index():
    return _Shell("docs-distiller", "Docs Distiller")


@rt("/docs-distiller")
def docs_distiller():
    return _Shell("docs-distiller", "Docs Distiller")


@rt("/youtube-search")
def youtube_search():
    return _Shell("youtube-search", "YouTube Content Search")


@rt("/coming-soon")
def coming_soon():
    return _Shell("coming-soon", "Coming Soon")


@rt("/health")
def health():
    return PlainTextResponse("OK")


if __name__ == "__main__":
    serve()
