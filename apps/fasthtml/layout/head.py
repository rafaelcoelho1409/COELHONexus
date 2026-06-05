"""Global HEAD — fonts, third-party CDN scripts/styles, our /static CSS.

Sent on every page. Library version pins keep the asset graph stable; bumps
are intentional and tested. Browser caches everything aggressively (jsDelivr
+ cdnjs + cloudflare) so the cost is one-off per visitor."""
from fasthtml.common import Link, Meta, Script


HEAD = (
    Meta(charset = "UTF-8"),
    Meta(name = "viewport", content = "width=device-width, initial-scale=1.0"),
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
    # Cytoscape.js — DAG canvas for per-stage LangGraph visualization.
    # ~320 KB minified, browser-cached aggressively. Dagre + cytoscape-
    # dagre add ~140 KB total for hierarchical DAG layout.
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
    Link(rel = "stylesheet", href = "/static/css/base.css"),
    Link(rel = "stylesheet", href = "/static/css/components.css"),
    Link(rel = "stylesheet", href = "/static/css/home.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/picker.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/ingestion.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/planner.css"),
    Link(rel = "stylesheet", href = "/static/css/dd/study.css"),
    Link(rel = "stylesheet", href = "/static/css/youtube.css"),
    Link(rel = "stylesheet", href = "/static/css/settings.css"),
    # Polls /api/v1/docs-distiller/runs/active every 30s; toggles
    # .has-running on the matching nav-item. defer = doesn't block paint.
    Script(src = "/static/js/topbar.js", defer = True),
)
