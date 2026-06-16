"""Step 4 · Query — SOTA-ish workbench layout.

Left half — CodeMirror 6 editor (top) + AI prompt panel (bottom).
Right half — results pane (per-backend renderer: ES table / Qdrant
cards / Neo4j graph + table + JSON).

The backend (ES / Qdrant / Neo4j) is picked in the shell's row-3 pill
strip (`shared/toolbar.py` → `query/chrome.py`). `query.js` reads
`data-query-backend` from the strip and:
  1. Swaps the CodeMirror language (`json` for ES + Qdrant, Cypher
     StreamLanguage for Neo4j) and the default-document scaffold.
  2. Re-dispatches POST `/api/v1/ycs/query/raw/{backend}` with the
     editor body on Run.
  3. Picks the matching renderer for the right pane.

Markup is intentionally light — every interactive element either has
a stable `id` or a `data-query-*` attribute so `query/editor.js` and
`query/renderers.js` can wire them without spelunking through deep
DOM trees."""
from __future__ import annotations

from fasthtml.common import Button, Div, H3, Span, Textarea


def _EditorHeader():
    """Tiny strip above the CodeMirror mount — current backend name +
    namespace caption + Run button. The pill that picks the backend
    lives in row 3 (shared/toolbar.py) — this is just status + actions."""
    return Div(
        Div(
            Span("Backend",
                 cls = "ycs-query-eh-label"),
            Span(
                "Elasticsearch",
                id  = "ycs-query-eh-backend",
                cls = "ycs-query-eh-backend",
            ),
            Span(
                "",
                id  = "ycs-query-eh-namespace",
                cls = "ycs-query-eh-namespace",
            ),
            cls = "ycs-query-eh-meta",
        ),
        Div(
            Span(
                "Ready",
                id  = "ycs-query-eh-status",
                cls = "ycs-query-eh-status",
            ),
            Button(
                "History",
                type  = "button",
                id    = "ycs-query-history-toggle",
                cls   = "btn-secondary ycs-query-history-toggle",
                title = "Show recent queries",
            ),
            Button(
                "Run",
                type = "button",
                id   = "ycs-query-run",
                cls  = "btn-primary ycs-query-run-btn",
                title = "Run the editor's contents (Ctrl/Cmd+Enter)",
            ),
            cls = "ycs-query-eh-actions",
        ),
        cls = "ycs-query-editor-header",
        id  = "ycs-query-editor-header",
    )


def _EditorMount():
    """Empty div CodeMirror attaches to. The class chains let the CSS
    target the mount itself (overflow + min-height) without leaking
    into the editor's own internal nodes."""
    return Div(
        "",
        id  = "ycs-query-editor",
        cls = "ycs-query-editor-mount",
        # `data-cm-loading` keeps the placeholder text visible until
        # CM6 swaps it for the actual editor DOM (see editor.js init).
        data_cm_loading = "true",
    )


def _AIPanel():
    """Prompt textarea + Generate button + status. Phase 4 wires this
    to `/api/v1/ycs/query/ai/{backend}` and streams generated tokens
    into the CodeMirror editor above. Until Phase 4 lands the button
    just shows a "coming soon" notice."""
    return Div(
        Div(
            Span("Ask AI", cls = "ycs-query-ai-title"),
            Span(
                "Describe what you need; the result fills the editor above.",
                cls = "ycs-query-ai-sub",
            ),
            cls = "ycs-query-ai-head",
        ),
        Textarea(
            name        = "prompt",
            id          = "ycs-query-ai-prompt",
            rows        = "3",
            placeholder = "e.g. List the 10 most-cited papers about agentic RAG…",
            cls         = "ycs-query-ai-input",
        ),
        Div(
            Span("", id = "ycs-query-ai-status", cls = "ycs-query-ai-status"),
            # Model chip — populated by the SSE `model` frame the server
            # emits once the FGTS-VA bandit selects the arm for this
            # call. Hidden when empty so the row collapses cleanly.
            Span(
                Span("Model", cls = "ycs-query-ai-model-label"),
                Span("",      id  = "ycs-query-ai-model-name",
                              cls = "ycs-query-ai-model-name"),
                id    = "ycs-query-ai-model",
                cls   = "ycs-query-ai-model",
                title = "LLM arm selected by the FGTS-VA bandit for this generation",
                hidden = True,
            ),
            Button(
                "Stop",
                type  = "button",
                id    = "ycs-query-ai-stop",
                cls   = "btn-secondary ycs-query-ai-stop",
                style = "display:none;",
            ),
            Button(
                "Generate",
                type = "button",
                id   = "ycs-query-ai-go",
                cls  = "btn-primary ycs-query-ai-go",
                title = "Generate a query (Ctrl/Cmd+Shift+Enter)",
            ),
            cls = "ycs-query-ai-actions",
        ),
        cls = "ycs-query-ai-zone",
    )


def _ResultsHeader():
    """Right-pane top strip — namespace echo + stats + view-mode
    toggle (Neo4j only; ES + Qdrant ignore it). `query/renderers.js`
    flips a `data-view-mode` attribute on `#ycs-query-results` so the
    CSS shows/hides the matching sub-region."""
    return Div(
        Div(
            Span("Results", cls = "ycs-query-rh-title"),
            Span("", id = "ycs-query-rh-stats", cls = "ycs-query-rh-stats"),
            cls = "ycs-query-rh-left",
        ),
        Div(
            Button(
                "Graph",
                type = "button",
                cls  = "ycs-query-view-btn",
                data_view_mode = "graph",
                aria_pressed   = "false",
            ),
            Button(
                "Table",
                type = "button",
                cls  = "ycs-query-view-btn active",
                data_view_mode = "table",
                aria_pressed   = "true",
            ),
            Button(
                "JSON",
                type = "button",
                cls  = "ycs-query-view-btn",
                data_view_mode = "json",
                aria_pressed   = "false",
            ),
            cls = "ycs-query-view-toggle",
            id  = "ycs-query-view-toggle",
        ),
        cls = "ycs-query-results-header",
    )


def _ResultsBody():
    """Three sibling panels, one per Neo4j view. ES + Qdrant only
    render into `#ycs-query-results-table` (they ignore graph + json).
    `renderers.js` empties and re-fills these on every Run."""
    return Div(
        Div(
            "",
            id  = "ycs-query-notice",
            cls = "ycs-query-notice",
            role = "status",
            aria_live = "polite",
        ),
        Div(
            "",
            id  = "ycs-query-results-graph",
            cls = "ycs-query-results-graph",
        ),
        Div(
            "",
            id  = "ycs-query-results-table",
            cls = "ycs-query-results-table",
        ),
        Div(
            "",
            id  = "ycs-query-results-json",
            cls = "ycs-query-results-json",
        ),
        id  = "ycs-query-results",
        cls = "ycs-query-results",
        data_view_mode = "table",
    )


def _EmptyState():
    """First-load welcome card. `editor.js` swaps the editor's default
    text per backend; this card explains the workflow. Hidden once any
    Run / AI generation completes."""
    return Div(
        H3("YCS Query Workbench", cls = "ycs-query-empty-title"),
        Span(
            "Edit raw DSL on the left and Run, or use Ask AI to "
            "describe what you want and fill the editor automatically. "
            "Switch backends in the row above.",
            cls = "ycs-query-empty-intro",
        ),
        cls = "ycs-query-empty",
        id  = "ycs-query-empty",
    )


def QueryBody(slug: str | None):
    """Two-column grid: editor + AI on the left, results on the right.

    `slug` is unused — the workbench browses the whole namespace, not
    a per-library slice. Kept in the signature for routes.py symmetry."""
    return Div(
        # ----- LEFT — editor + AI -----------------------------------
        Div(
            _EditorHeader(),
            _EditorMount(),
            _AIPanel(),
            cls = "ycs-query-left",
        ),
        # ----- RIGHT — results --------------------------------------
        Div(
            _ResultsHeader(),
            _EmptyState(),
            _ResultsBody(),
            cls = "ycs-query-right",
        ),
        cls = "ycs-query-layout",
    )
