# FastHTML Restructure Plan — 2026-05-22

Target: `apps/fasthtml/`

## Python

```
apps/fasthtml/
  main.py                     # app entry, mount routes
  shell.py                    # page chrome (_Shell, HEAD)
  proxy.py                    # /api proxy -> FastAPI
  routes.py                   # /health, /coming-soon

  features/
    __init__.py
    home.py                   # / landing page
    docs_distiller.py         # /docs-distiller wizard HTML
    youtube_content_search.py # -> "Coming Soon" placeholder

  Dockerfile.fasthtml
  entrypoint.sh
  pyproject.toml
```

## Static — CSS

```
static/css/
  base.css                    # :root vars, reset, typography, .page, .card, .topbar, .nav, .btn-* (lines 1-131)
  components.css              # stepper, spinner, modal, notices, file drawer (shared UI, lines 259-773)
  dd/
    picker.css                # framework picker Step 1 (lines 133-258)
    ingestion.css             # sidebar layout + progress + page list (lines 355-689)
    planner.css               # planner cards, node drawer, graph canvas, KPI grid (lines 774-1472)
    study.css                 # Step 5 study viewer + chapter strip (lines 2064-2639)
  home.css                    # hero, stats, feature cards, footer (lines 1473-1701)
  youtube.css                 # YCS wizard, keep as-is for future (lines 1702-2063)
```

## Static — JS

All DD JavaScript uses ES module pattern (`<script type="module">`).

```
static/js/dd/
  state.js       # ~90 lines   — shared DOM refs, state vars, API constant
  utils.js       # ~80 lines   — sleep, fmtBytes, fmtAge, escapeHtml
  ui.js          # ~200 lines  — toast, modal, drawer, stepper navigation
  picker.js      # ~150 lines  — filter, chip selection, tile rendering
  ingestion.js   # ~300 lines  — progress polling, manifest display, triggerIngest
  library.js     # ~200 lines  — sidebar, loadLibrary, recoverActiveRuns
  planner.js     # ~1600 lines — Cytoscape graph, cards, SSE, start/cancel/wipe
  synth.js       # ~1400 lines — Cytoscape graph, cards, SSE, start/cancel/wipe
  study.js       # ~600 lines  — chapter viewer, flashcards, tabs, artifacts
  main.js        # ~100 lines  — boot: init DOM, bind listeners, recovery
```

## Rationale

- **Domain-first static grouping**: `css/dd/`, `js/dd/` mirrors the `domains/dd/` backend pattern
- **Shared CSS at root**: `base.css`, `components.css` used by all features
- **Tier files stay flat in JS**: planner.js and synth.js are large but self-contained feature modules
- **YouTube -> Coming Soon**: placeholder page only, full implementation deferred
