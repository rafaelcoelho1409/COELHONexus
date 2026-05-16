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
    ("youtube-content-search", "YouTube Content Search", "/youtube-content-search"),
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
        .page { padding: 32px 40px 96px 40px; }
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
        .fw-grid.fw-grid-empty::after {
          content: 'No frameworks match this filter.';
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
          height: 28px;
          width: auto;
          max-width: 100%;
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
        .fw-sticky-bar {
          position: fixed;
          bottom: 0;
          left: 0;
          right: 0;
          background: var(--card);
          border-top: 1px solid var(--border);
          box-shadow: 0 -2px 12px rgba(0, 0, 0, 0.05);
          padding: 14px 40px;
          display: flex;
          align-items: center;
          gap: 16px;
          z-index: 50;
          transform: translateY(100%);
          transition: transform 0.25s ease;
        }
        .fw-sticky-bar.visible { transform: translateY(0); }
        .fw-selected-label {
          flex: 1;
          color: var(--text-muted);
          font-size: 0.88rem;
        }
        .fw-selected-name {
          color: var(--primary);
          font-weight: 600;
        }
        .fw-empty {
          color: var(--text-muted);
          padding: 32px;
          text-align: center;
          font-size: 0.9rem;
        }

        /* ===== Stepper ===== */
        .fw-stepper-row {
          display: flex;
          align-items: center;
          gap: 16px;
          margin-bottom: 32px;
        }
        #fw-step-1-edit {
          display: flex;
          flex-direction: column;
          gap: 18px;
        }
        .fw-stepper {
          flex: 1;
          display: flex;
          align-items: center;
        }
        .fw-step {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-shrink: 0;
          cursor: not-allowed;
          user-select: none;
          opacity: 0.5;
          transition: opacity 0.2s;
        }
        .fw-step.completed,
        .fw-step.active {
          cursor: pointer;
          opacity: 1;
        }
        .fw-step-circle {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          border: 2px solid var(--border);
          background: var(--card);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 0.78rem;
          font-weight: 600;
          color: var(--text-muted);
          transition: all 0.2s;
        }
        .fw-step.active .fw-step-circle {
          border-color: var(--primary);
          color: var(--primary);
        }
        .fw-step.completed .fw-step-circle {
          background: var(--primary);
          border-color: var(--primary);
          color: #fff;
        }
        .fw-step-label {
          font-size: 0.85rem;
          font-weight: 500;
          color: var(--text-muted);
          letter-spacing: 0.02em;
          white-space: nowrap;
        }
        .fw-step.active .fw-step-label {
          color: var(--primary);
          font-weight: 600;
        }
        .fw-step.completed .fw-step-label { color: var(--text); }
        .fw-step-connector {
          flex: 1;
          height: 2px;
          background: var(--border);
          margin: 0 14px;
          min-width: 24px;
          transition: background 0.2s;
        }
        .fw-step-connector.complete { background: var(--primary); }
        .fw-new-study {
          color: var(--primary);
          font-size: 0.82rem;
          font-weight: 500;
          cursor: pointer;
          background: none;
          border: 0;
          padding: 6px 10px;
          font-family: inherit;
          letter-spacing: 0.02em;
          visibility: hidden;
          white-space: nowrap;
        }
        .fw-new-study.visible { visibility: visible; }
        .fw-new-study:hover { color: var(--primary-dark); }

        /* Step content panels — only one is visible at a time */
        .fw-step-panel { display: none; }
        .fw-step-panel.active { display: block; }

        /* Read-only summary box used when revisiting completed steps */
        .fw-readonly {
          padding: 18px 20px;
          background: rgba(0, 0, 0, 0.02);
          border: 1px solid var(--border);
          border-radius: 4px;
          color: var(--text);
          font-size: 0.92rem;
          line-height: 1.5;
        }
        .fw-readonly .fw-readonly-name {
          color: var(--primary);
          font-weight: 600;
        }
        .fw-readonly-hint {
          display: block;
          margin-top: 10px;
          font-size: 0.78rem;
          color: var(--text-muted);
        }

        /* Placeholder body for not-yet-wired steps */
        .fw-step-placeholder {
          padding: 64px 24px;
          text-align: center;
          color: var(--text-muted);
          font-size: 0.92rem;
          border: 2px dashed var(--border);
          border-radius: 4px;
          line-height: 1.6;
        }
        .fw-step-placeholder-title {
          font-size: 1.1rem;
          font-weight: 500;
          color: var(--text);
          margin-bottom: 10px;
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
  // ----- picker controls -----
  const search = document.querySelector('#fw-search');
  const chips = document.querySelectorAll('.fw-chip');
  const tiles = document.querySelectorAll('.fw-tile');
  const grid = document.querySelector('#fw-grid');
  const countEl = document.querySelector('#fw-count');
  const total = tiles.length;
  // ----- sticky action bar -----
  const generate = document.querySelector('#fw-generate');
  const selectedName = document.querySelector('#fw-selected-name');
  const stickyBar = document.querySelector('#fw-sticky-bar');
  // ----- stepper -----
  const steps = document.querySelectorAll('.fw-step');
  const connectors = document.querySelectorAll('.fw-step-connector');
  const panels = document.querySelectorAll('.fw-step-panel');
  const newStudy = document.querySelector('#fw-new-study');
  const readonlyName = document.querySelector('#fw-readonly-name');
  const step1Edit = document.querySelector('#fw-step-1-edit');
  const step1Readonly = document.querySelector('#fw-step-1-readonly');

  let activeChip = 'All';
  let query = '';
  let selected = null;
  let currentStep = 1;     // which panel is rendered
  let farthestStep = 1;    // highest step ever reached (controls click-back access)

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
    countEl.textContent = visible + ' of ' + total;
  }

  function renderStepper() {
    steps.forEach((s, i) => {
      const n = i + 1;
      s.classList.remove('active', 'completed');
      if (n === currentStep) s.classList.add('active');
      else if (n <= farthestStep) s.classList.add('completed');
    });
    connectors.forEach((c, i) => {
      c.classList.toggle('complete', i + 1 < farthestStep);
    });
  }

  function showStep(n) {
    if (n > farthestStep) return;
    currentStep = n;
    panels.forEach((p, i) => p.classList.toggle('active', i + 1 === n));
    // Step 1 has two sub-views: editable (only when we've never advanced)
    // and read-only (once farthestStep > 1, Step 1 is locked for re-edit).
    if (step1Edit && step1Readonly) {
      const editable = (currentStep === 1 && farthestStep === 1);
      step1Edit.style.display = editable ? '' : 'none';
      step1Readonly.style.display = editable ? 'none' : '';
      if (!editable) readonlyName.textContent = selected || '—';
    }
    // Sticky bar only on the editable Step 1 with a selection.
    stickyBar.classList.toggle(
      'visible', n === 1 && farthestStep === 1 && selected !== null
    );
    // "+ New Study" link appears once the user has advanced past Step 1.
    newStudy.classList.toggle('visible', farthestStep > 1);
    renderStepper();
  }

  function advance() {
    if (currentStep >= 3) return;
    farthestStep = Math.max(farthestStep, currentStep + 1);
    showStep(currentStep + 1);
  }

  function resetAll() {
    selected = null;
    currentStep = 1;
    farthestStep = 1;
    tiles.forEach(t => t.classList.remove('selected'));
    selectedName.textContent = '';
    stickyBar.classList.remove('visible');
    showStep(1);
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
    // Picker is read-only once we've advanced past Step 1.
    if (currentStep !== 1 || farthestStep !== 1) return;
    tiles.forEach(x => x.classList.remove('selected'));
    t.classList.add('selected');
    selected = t.dataset.name;
    selectedName.textContent = selected;
    stickyBar.classList.add('visible');
  }));

  steps.forEach((s, i) => s.addEventListener('click', () => {
    const target = i + 1;
    if (target <= farthestStep) showStep(target);
  }));

  generate.addEventListener('click', () => {
    if (!selected) return;
    advance();
    // TODO: kick off SSE pipeline run for `selected`
    console.log('Generate study for:', selected);
  });

  newStudy.addEventListener('click', resetAll);

  countEl.textContent = total + ' of ' + total;
  renderStepper();
})();
"""


def _Step(n: int, label: str, active: bool = False):
    """One stepper step (circle + label)."""
    cls = "fw-step"
    if active:
        cls += " active"
    return Div(
        Span(str(n), cls="fw-step-circle"),
        Span(label, cls="fw-step-label"),
        cls=cls,
        id=f"fw-step-{n}",
        data_step=str(n),
    )


def _Picker():
    """Stepper-driven Docs Distiller body:
      Step 1 Pick     — framework picker (editable while farthestStep == 1)
      Step 2 Generate — placeholder for the SSE pipeline view
      Step 3 Study    — placeholder for chapter output
    """
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

    # ---- Step 1 panel: editable picker + read-only summary ----
    step1_edit = Div(
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
        id="fw-step-1-edit",
    )
    step1_readonly = Div(
        "Framework: ",
        Span("—", id="fw-readonly-name", cls="fw-readonly-name"),
        Span(
            "Selection is locked while the pipeline runs. "
            "Use “+ New Study” to start over.",
            cls="fw-readonly-hint",
        ),
        id="fw-step-1-readonly",
        cls="fw-readonly",
        style="display: none;",
    )

    # ---- Step 2 / Step 3 placeholder panels (wired later) ----
    step2_panel = Div(
        Div(
            Div("Generation pipeline", cls="fw-step-placeholder-title"),
            "Live SSE stream of Resolver → Ingestion → Planner → Synth → "
            "Curator → Assembler will render here once wired to the backend.",
            cls="fw-step-placeholder",
        ),
        id="fw-step-2-panel",
        cls="fw-step-panel",
    )
    step3_panel = Div(
        Div(
            Div("Study output", cls="fw-step-placeholder-title"),
            "Generated chapter READMEs, challenges, and flashcards appear "
            "here when the pipeline finishes.",
            cls="fw-step-placeholder",
        ),
        id="fw-step-3-panel",
        cls="fw-step-panel",
    )

    return Div(
        # Stepper row: progress indicator + "+ New Study" reset link
        Div(
            Div(
                _Step(1, "Pick", active=True),
                Span(cls="fw-step-connector"),
                _Step(2, "Generate"),
                Span(cls="fw-step-connector"),
                _Step(3, "Study"),
                cls="fw-stepper",
            ),
            Button("+ New Study", id="fw-new-study", cls="fw-new-study"),
            cls="fw-stepper-row",
        ),
        # Step 1 panel
        Div(
            step1_edit,
            step1_readonly,
            id="fw-step-1-panel",
            cls="fw-step-panel active",
        ),
        # Steps 2 + 3
        step2_panel,
        step3_panel,
        # Sticky action bar (only shows on Step 1 editable + a selection)
        Div(
            Span(
                "Selected: ",
                Span("", id="fw-selected-name", cls="fw-selected-name"),
                id="fw-selected-label",
                cls="fw-selected-label",
            ),
            Button("Generate Study", id="fw-generate", cls="btn-primary"),
            id="fw-sticky-bar",
            cls="fw-sticky-bar",
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


@rt("/youtube-content-search")
def youtube_search():
    return _Shell("youtube-content-search", "YouTube Content Search")


@rt("/coming-soon")
def coming_soon():
    return _Shell("coming-soon", "Coming Soon")


@rt("/health")
def health():
    return PlainTextResponse("OK")


if __name__ == "__main__":
    serve()
