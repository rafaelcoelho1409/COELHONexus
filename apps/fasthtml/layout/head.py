"""Global HEAD — fonts, third-party CDN scripts/styles, our /static CSS.

Sent on every page. Library version pins keep the asset graph stable; bumps
are intentional and tested. Browser caches everything aggressively (jsDelivr
+ cdnjs + cloudflare) so the cost is one-off per visitor.

Cascade order is controlled by `@layer` declarations inside the CSS files
themselves (Phase B). The link order in HEAD is no longer load-bearing —
explicit `@layer reset, base, components, layout, features, overrides;`
in tokens.css establishes the order regardless of when each file is fetched.

Module specifiers are aliased via an inline import map (Phase F). Code
imports `@dd/shared/state.js` instead of `../../shared/state.js` —
folder renames touch the map only, not every consumer."""
from fasthtml.common import Link, Meta, NotStr, Script


_IMPORTMAP = NotStr("""{
  "imports": {
    "@nx/":           "/static/js/",
    "@nx/stores/":    "/static/js/stores/",
    "@dd/":           "/static/js/dd/",
    "@dd/shared/":    "/static/js/dd/shared/",
    "@dd/catalog/":   "/static/js/dd/catalog/",
    "@dd/ingestion/": "/static/js/dd/ingestion/",
    "@dd/planner/":   "/static/js/dd/planner/",
    "@dd/synth/":     "/static/js/dd/synth/",
    "@dd/study/":     "/static/js/dd/study/",
    "@ycs/":          "/static/js/ycs/",
    "nanostores":     "https://esm.sh/nanostores@1",
    "@codemirror/state":    "https://esm.sh/@codemirror/state@6.4.1",
    "@codemirror/view":     "https://esm.sh/@codemirror/view@6.26.3?external=@codemirror/state",
    "@codemirror/language": "https://esm.sh/@codemirror/language@6.10.1?external=@codemirror/state,@codemirror/view",
    "@codemirror/commands": "https://esm.sh/@codemirror/commands@6.5.0?external=@codemirror/state,@codemirror/view,@codemirror/language",
    "@codemirror/autocomplete":  "https://esm.sh/@codemirror/autocomplete@6.16.0?external=@codemirror/state,@codemirror/view,@codemirror/language",
    "@codemirror/lang-json":     "https://esm.sh/@codemirror/lang-json@6.0.1?external=@codemirror/state,@codemirror/view,@codemirror/language,@codemirror/autocomplete",
    "@codemirror/legacy-modes/mode/cypher": "https://esm.sh/@codemirror/legacy-modes@6.4.0/mode/cypher?external=@codemirror/language",
    "tabulator-tables":   "https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/js/tabulator_esm.min.mjs",
    "vanilla-jsoneditor": "https://cdn.jsdelivr.net/npm/vanilla-jsoneditor@3.12.0/standalone.js"
  }
}""")


HEAD = (
    Meta(charset = "UTF-8"),
    Meta(name = "viewport", content = "width=device-width, initial-scale=1.0"),
    # Import map MUST come before any module <script> that uses it
    # (browser spec; import maps are immutable once the first module
    # loads). Inline form is the broadest-supported variant — external
    # `<script type="importmap" src=...>` only shipped in Chrome 116+
    # (2023) and Firefox/Safari haven't fully caught up.
    Script(_IMPORTMAP, type = "importmap"),
    Link(rel = "preconnect", href = "https://fonts.googleapis.com"),
    Link(rel = "preconnect", href = "https://fonts.gstatic.com", crossorigin = ""),
    Link(
        rel = "stylesheet",
        href = (
            "https://fonts.googleapis.com/css2?"
            "family=Raleway:wght@300;400;500;600;700&display=swap"
        ),
    ),
    # Client-side markdown renderer for the file-content drawer + Study
    # chapter viewer. Pinned major version — zero deps, ~50 KB gzip.
    Script(src = "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"),
    # highlight.js — most-adopted SOTA highlighter as of 2026 (10M wk
    # downloads, zero-config auto-detect, 190+ langs, no dependencies).
    # Theme: GitHub Dark, matches the burgundy + sharp-radius aesthetic.
    Link(
        rel = "stylesheet",
        href = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/styles/github-dark-dimmed.min.css",
    ),
    Script(
        src = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js",
        defer = True,
    ),
    # DOMPurify sanitizes marked.parse() output before it's inserted in
    # the Study viewer (chapter markdown is untrusted LLM output).
    # KaTeX CSS styles math rendered by marked-katex-extension. mermaid
    # + marked extensions are dynamically imported in study.js.
    Script(
        src = "https://cdn.jsdelivr.net/npm/dompurify@3.2.6/dist/purify.min.js",
        defer = True,
    ),
    Link(
        rel = "stylesheet",
        href = "https://cdn.jsdelivr.net/npm/katex@0.16.22/dist/katex.min.css",
    ),
    # Cytoscape + Dagre + cytoscape-dagre eagerly loaded in HEAD with
    # `defer=True` — matches OLD reference (commit f5bff8e). The Phase-2
    # (2026-06-05) lazy-load via `shared/cytoscape_loader.js::ensureCytoscape()`
    # was an admirable bandwidth optimization for non-planner pages, BUT
    # dynamic `<script>` injection's load-order semantics are fragile under
    # adblockers + slow CDN connections — and the failure mode is silent
    # (planner page renders the canvas container at 720px but with no
    # Cytoscape inside, so the user sees an empty white box). Eager-load
    # with browser caching is the OLD's empirical-good-enough.
    # `cytoscape_loader.js::ensureCytoscape()` stays as a no-op when
    # `window.cytoscape` is already defined, so existing callers
    # short-circuit cleanly.
    Script(
        src = "https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.30.4/cytoscape.min.js",
        defer = True,
    ),
    Script(
        src = "https://unpkg.com/dagre@0.8.5/dist/dagre.min.js",
        defer = True,
    ),
    Script(
        src = "https://unpkg.com/cytoscape-dagre@2.5.0/cytoscape-dagre.js",
        defer = True,
    ),
    # cytoscape-fcose — force-directed layout for the YCS Query graph
    # view (Neo4j-Browser look). UMD bundle exposes window.cytoscapeFcose;
    # the renderer registers it once via cytoscape.use() before first use.
    Script(
        src = "https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js",
        defer = True,
    ),
    # Tabulator — SOTA vanilla-JS data grid (MIT). Drives the Table view
    # mode on the YCS Query page (ES hits, Qdrant points, Neo4j rows).
    # Loaded globally so the stylesheet is cached across page transitions;
    # the ESM bundle is pulled in via the importmap from renderers.js.
    Link(
        rel = "stylesheet",
        href = "https://cdn.jsdelivr.net/npm/tabulator-tables@6.3.0/dist/css/tabulator.min.css",
    ),
    # Tokens (Phase B) — declares `@layer reset, base, components, layout,
    # features, overrides;` so all subsequent stylesheets land in the right
    # cascade slot regardless of fetch order. Must load first among ours.
    Link(rel = "stylesheet", href = "/static/css/tokens.css"),
    # base.css split per-section (Item 5, 2026-06-05): :root tokens
    # consolidated into tokens.css; remaining content split into reset
    # (html/body + view-transitions + skip-link), shell (.shell grid +
    # .page scroll region), and topbar (sticky-wrap + nav-items +
    # status-dot + feature row + buttons).
    Link(rel = "stylesheet", href = "/static/css/base/reset.css"),
    Link(rel = "stylesheet", href = "/static/css/base/shell.css"),
    Link(rel = "stylesheet", href = "/static/css/base/topbar.css"),
    # components.css split per-component (Item 4, 2026-06-05): panels,
    # sub-nav (.dd-substage), toolbar (+ category filter), framework
    # picker dropdown popover (.dd-fw-picker), overlays (spinner +
    # confirm modal + notices + file drawer).
    Link(rel = "stylesheet", href = "/static/css/components/panels.css"),
    Link(rel = "stylesheet", href = "/static/css/components/sub_nav.css"),
    Link(rel = "stylesheet", href = "/static/css/components/toolbar.css"),
    Link(rel = "stylesheet", href = "/static/css/components/framework_picker.css"),
    Link(rel = "stylesheet", href = "/static/css/components/overlays.css"),
    Link(rel = "stylesheet", href = "/static/css/home/home.css"),
    # dd/shared/picker.css split (Item 6, 2026-06-05): catalog-specific
    # tile-grid + sticky-bar moved to dd/catalog/catalog.css; remaining
    # shared/picker.css holds only the layout wrapper + generic empty state.
    Link(rel = "stylesheet", href = "/static/css/dd/shared/picker.css"),
    # dd/shared/markdown.css — unscoped `.fw-markdown` / `.fw-code-*` /
    # `.fw-terminal` / `.fw-mermaid` / `.fw-page-card.viewing` rules
    # that need to apply across EVERY drawer + chapter view (Ingestion
    # `aside#fw-drawer`, Planner `.fw-node-drawer`, Study `.fw-study-pane`).
    # Without this the Ingestion .md drawer rendered the body with browser
    # defaults — no monospace, no code background, no heading hierarchy.
    Link(rel = "stylesheet", href = "/static/css/dd/shared/markdown.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/catalog/catalog.css"),
    # ingestion.css split per-section (Phase 1, 2026-06-05): layout +
    # library + progress + pages. Same load-order-doesn't-matter logic
    # as the planner files — @layer + @scope in each file handles it.
    Link(rel = "stylesheet", href = "/static/css/dd/ingestion/layout.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/ingestion/library.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/ingestion/progress.css"),
    # explorer.css replaces the legacy pages.css flat-grid styles
    # (2026-06-08) — the Ingestion page is now a split-pane docs
    # explorer with search/filter/tree on the left and live markdown
    # preview on the right.
    Link(rel = "stylesheet", href = "/static/css/dd/ingestion/explorer.css"),
    # planner.css split per-section (Phase B+1, 2026-06-05): drawer +
    # cards + canvas.
    Link(rel = "stylesheet", href = "/static/css/dd/planner/drawer.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/planner/cards.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/planner/canvas.css"),
    # synth/chstrip.css extracted from study/tabs.css (Phase 1, 2026-06-05)
    # — chstrip lives on the synth page (inside `.fw-synth-split`), so it
    # needs its own @scope. Leaving it under `.fw-study-pane` would have
    # silently broken the chapter-strip styles.
    Link(rel = "stylesheet", href = "/static/css/dd/synth/chstrip.css"),
    # pipeline.css — unified Planner + Synth page layout (2026-06-08).
    # Stacks the two Cytoscape canvases vertically on the left, chstrip
    # on the right. Each zone is tinted (primary for planner, ingested-
    # green for synth) so the user can tell at a glance which is which.
    Link(rel = "stylesheet", href = "/static/css/dd/pipeline/pipeline.css"),
    # study.css split per-section (Phase 1, 2026-06-05): layout +
    # sidebar + rail + reader + tabs.
    Link(rel = "stylesheet", href = "/static/css/dd/study/layout.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/study/sidebar.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/study/rail.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/study/reader.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/study/tabs.css"),
    Link(rel = "stylesheet", href = "/static/css/ycs/ycs.css"),
    # Research Radar (step 5b, 2026-06-12) — scan form + status strip + digest cards
    Link(rel = "stylesheet", href = "/static/css/rr/rr.css"),
    Link(rel = "stylesheet", href = "/static/css/settings/settings.css"),
    # Polls /api/v1/docs-distiller/runs/active every 30s; toggles
    # .has-running on the matching nav-item. defer = doesn't block paint.
    Script(src = "/static/js/topbar.js", defer = True),
)
