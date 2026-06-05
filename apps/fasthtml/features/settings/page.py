"""Settings body — BYOK provider keys + free-model selection.

Server renders only the skeleton; `static/js/settings.js` populates it
from `/api/v1/llm/settings/*` (proxied to FastAPI). Raw keys go
browser → FastAPI on save and are NEVER returned — responses carry
masked status only."""
from fasthtml.common import Button, Div, P, Script, Span


def SettingsBody():
    return Div(
        Div(
            P(
                "Choose the AI providers and free models COELHO Nexus may use. "
                "Keys are encrypted and stored on the server — they're never sent "
                "back to your browser, and they survive restarts.",
                cls = "settings-intro",
            ),
            # Filled by JS from /providers (.ready / .missing_required).
            # Hidden until populated. Surfaces the NVIDIA NIM requirement
            # (embeddings + reranking) prominently.
            Div("", id = "set-readiness", cls = "set-readiness", role = "status"),
            Div(
                Button(
                    "Enable all keyed providers",
                    cls = "set-btn set-btn-ghost",
                    id = "set-enable-all",
                    type = "button",
                ),
                Button(
                    "Test all",
                    cls = "set-btn set-btn-ghost",
                    id = "set-test-all",
                    type = "button",
                ),
                Span("", cls = "set-global-note", id = "set-global-note"),
                cls = "settings-actions",
            ),
            Div(
                Div("Loading providers…", cls = "set-loading"),
                id = "settings-providers",
                cls = "settings-providers",
            ),
            cls = "settings-root",
            id = "settings-root",
        ),
        Div("", id = "set-toast", cls = "set-toast", aria_live = "polite"),
        Script(src = "/static/js/settings.js", type = "module"),
    )
