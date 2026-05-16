"""COELHO Nexus — FastHTML base shell.

Visual style derived from the Plotly dash-financial-report sample app:
Raleway typeface, burgundy (#c41230) accent on off-white (#fafafa). Three
feature routes are wired in (Docs Distiller, YouTube Content Search, and a
placeholder slot). Docs Distiller renders the framework picker (search +
category chips + tile grid backed by FastAPI's /api/v1/frameworks).
"""
import os

import httpx
from fasthtml.common import (
    H1, A, Button, Div, Img, Input, Link, Meta, P, Script, Span, Style, Title,
    fast_app, serve,
)
from starlette.responses import PlainTextResponse


FEATURES = [
    ("docs-distiller", "Docs Distiller", "/docs-distiller"),
    ("youtube-search", "YouTube Content Search", "/youtube-search"),
    ("coming-soon", "Coming Soon", "/coming-soon"),
]

FASTAPI_URL = os.environ.get(
    "FASTAPI_URL", "http://coelhonexus-fastapi:8000"
).rstrip("/")


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
        .page { padding: 32px 40px; }
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
        .panel { min-height: 360px; }

        /* ===== Framework picker ===== */
        .fw-picker { display: flex; flex-direction: column; gap: 18px; }
        .fw-search-row {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        .fw-search {
          flex: 1;
          padding: 12px 16px;
          font-size: 0.95rem;
          font-family: inherit;
          border: 1px solid var(--border);
          border-radius: 3px;
          background: var(--card);
          color: var(--text);
          outline: none;
          transition: border-color 0.15s;
        }
        .fw-search:focus { border-color: var(--primary); }
        .fw-count {
          color: var(--text-muted);
          font-size: 0.78rem;
          white-space: nowrap;
          letter-spacing: 0.02em;
        }
        .fw-chips { display: flex; flex-wrap: wrap; gap: 8px; }
        .fw-chip {
          padding: 6px 14px;
          border-radius: 999px;
          border: 1px solid var(--border);
          font-size: 0.78rem;
          color: var(--text-muted);
          cursor: pointer;
          user-select: none;
          background: var(--card);
          transition: color 0.15s, background 0.15s, border-color 0.15s;
        }
        .fw-chip:hover { color: var(--text); border-color: var(--text-muted); }
        .fw-chip.active {
          background: var(--primary);
          color: #fff;
          border-color: var(--primary);
        }
        .fw-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 10px;
        }
        .fw-grid.fw-grid-empty {
          display: block;
          padding: 24px 0;
          text-align: center;
          color: var(--text-muted);
          font-size: 0.85rem;
        }
        .fw-tile {
          padding: 14px;
          border: 1px solid var(--border);
          border-radius: 4px;
          cursor: pointer;
          background: var(--card);
          transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
        }
        .fw-tile:hover { border-color: var(--text-muted); }
        .fw-tile.selected {
          border-color: var(--primary);
          background: rgba(196, 18, 48, 0.04);
          box-shadow: inset 0 0 0 1px var(--primary);
        }
        .fw-tile-logo {
          width: 28px;
          height: 28px;
          display: block;
          margin-bottom: 10px;
          object-fit: contain;
        }
        .fw-tile-name {
          font-size: 0.92rem;
          font-weight: 500;
          color: var(--text);
          word-break: break-word;
        }
        .fw-tile-cat {
          font-size: 0.68rem;
          color: var(--text-muted);
          margin-top: 6px;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }
        .fw-actions {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 16px;
          padding-top: 16px;
          border-top: 1px solid var(--border);
          margin-top: 8px;
        }
        .fw-selected-label {
          color: var(--text-muted);
          font-size: 0.82rem;
          flex: 1;
        }
        .fw-generate[disabled] { opacity: 0.35; cursor: not-allowed; }
        .fw-generate[disabled]:hover { background: var(--primary); }
        .fw-empty {
          color: var(--text-muted);
          padding: 32px;
          text-align: center;
          font-size: 0.9rem;
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


def _Shell(active_key: str, title_text: str, body=None):
    """Card shell: topbar (brand + nav + Learn More) → red-accent title row
    → content panel. `body` is rendered inside the panel."""
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
                    cls="title-row",
                ),
                Div(body if body is not None else "", cls="panel"),
                cls="card",
            ),
            cls="page",
        ),
    )


def _fetch_frameworks() -> list[dict]:
    """Pull the catalog from FastAPI. Returns [] on error so the picker can
    render an empty state instead of 500ing the page."""
    try:
        r = httpx.get(f"{FASTAPI_URL}/api/v1/docs-distiller/frameworks", timeout=5.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


_PICKER_JS = """
(() => {
  const search = document.querySelector('#fw-search');
  const chips = document.querySelectorAll('.fw-chip');
  const tiles = document.querySelectorAll('.fw-tile');
  const grid = document.querySelector('#fw-grid');
  const generate = document.querySelector('#fw-generate');
  const selectedLabel = document.querySelector('#fw-selected-label');
  const countEl = document.querySelector('#fw-count');
  const total = tiles.length;

  let activeChip = 'All';
  let query = '';
  let selected = null;

  function applyFilter() {
    let visible = 0;
    tiles.forEach(t => {
      const name = t.dataset.name.toLowerCase();
      const cat = t.dataset.category;
      const matchQ = !query || name.includes(query);
      const matchC = activeChip === 'All' || cat === activeChip;
      const show = matchQ && matchC;
      t.style.display = show ? '' : 'none';
      if (show) visible++;
    });
    grid.classList.toggle('fw-grid-empty', visible === 0);
    if (visible === 0) {
      grid.textContent = 'No frameworks match this filter.';
    } else if (grid.children.length === 0) {
      // restore was emptied — only happens after the textContent path above
      location.reload();
    }
    countEl.textContent = visible + ' of ' + total;
  }

  search.addEventListener('input', e => {
    query = e.target.value.toLowerCase().trim();
    applyFilter();
  });

  chips.forEach(c => c.addEventListener('click', () => {
    chips.forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    activeChip = c.dataset.chip;
    applyFilter();
  }));

  tiles.forEach(t => t.addEventListener('click', () => {
    tiles.forEach(x => x.classList.remove('selected'));
    t.classList.add('selected');
    selected = t.dataset.name;
    selectedLabel.textContent = 'Selected: ' + selected;
    generate.removeAttribute('disabled');
  }));

  generate.addEventListener('click', () => {
    if (!selected) return;
    // TODO: kick off SSE pipeline run for `selected`
    console.log('Generate study for:', selected);
  });

  countEl.textContent = total + ' of ' + total;
})();
"""


def _Picker():
    """Search + category chips + tile grid, all driven by sources.yaml."""
    frameworks = _fetch_frameworks()
    if not frameworks:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/frameworks.",
                cls="fw-empty",
            ),
            cls="fw-picker",
        )

    cats = sorted({(f.get("category") or "Other") for f in frameworks})
    chips = [Span("All", cls="fw-chip active", data_chip="All")] + [
        Span(c, cls="fw-chip", data_chip=c) for c in cats
    ]
    def _tile(f):
        children = []
        if f.get("logo"):
            children.append(Img(src=f["logo"], alt="", cls="fw-tile-logo"))
        children.append(Div(f["name"], cls="fw-tile-name"))
        children.append(Div(f.get("category") or "—", cls="fw-tile-cat"))
        return Div(
            *children,
            cls="fw-tile",
            data_name=f["name"],
            data_category=(f.get("category") or "Other"),
        )

    tiles = [_tile(f) for f in frameworks]

    return Div(
        Div(
            Input(
                type="search",
                id="fw-search",
                placeholder=f"Search {len(frameworks)} frameworks…",
                autocomplete="off",
                autofocus=True,
                cls="fw-search",
            ),
            Span("", id="fw-count", cls="fw-count"),
            cls="fw-search-row",
        ),
        Div(*chips, cls="fw-chips"),
        Div(*tiles, cls="fw-grid", id="fw-grid"),
        Div(
            Span("Pick a framework above to enable generation.",
                 id="fw-selected-label", cls="fw-selected-label"),
            Button(
                "Generate Study",
                id="fw-generate",
                cls="btn-primary fw-generate",
                disabled=True,
            ),
            cls="fw-actions",
        ),
        Script(_PICKER_JS),
        cls="fw-picker",
    )


@rt("/")
def index():
    return _Shell("docs-distiller", "Docs Distiller", body=_Picker())


@rt("/docs-distiller")
def docs_distiller():
    return _Shell("docs-distiller", "Docs Distiller", body=_Picker())


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
