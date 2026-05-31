"""Global Settings — BYOK LLM provider keys + free-model selection.

A single global page (reachable from the gear in the topbar on every page)
where a non-technical user supplies provider API keys and picks which free
models the rotator may use — replacing "keys via Helm configmap". The backend
rotator is already global, so this page serves Docs Distiller, YouTube Content
Search, and every future feature at once.

Server renders only a skeleton; `static/js/settings.js` populates it from
`/api/v1/llm/settings/*` (proxied to FastAPI). Raw keys go browser→FastAPI on
save and are NEVER returned — responses carry masked status only.
"""
from fasthtml.common import Button, Div, P, Script, Span

from shell import _Shell


def _SettingsBody():
    return Div(
        Div(
            P(
                "Choose the AI providers and free models COELHO Nexus may use. "
                "Keys are encrypted and stored on the server — they're never sent "
                "back to your browser, and they survive restarts.",
                cls="settings-intro",
            ),
            # Readiness banner — JS fills it from /providers (.ready /
            # .missing_required). Hidden until populated. Surfaces the
            # NVIDIA NIM requirement (embeddings + reranking) prominently.
            Div("", id="set-readiness", cls="set-readiness", role="status"),
            Div(
                Button(
                    "Enable all keyed providers",
                    cls="set-btn set-btn-ghost",
                    id="set-enable-all",
                    type="button",
                ),
                Button(
                    "Test all",
                    cls="set-btn set-btn-ghost",
                    id="set-test-all",
                    type="button",
                ),
                Span("", cls="set-global-note", id="set-global-note"),
                cls="settings-actions",
            ),
            Div(
                Div("Loading providers…", cls="set-loading"),
                id="settings-providers",
                cls="settings-providers",
            ),
            cls="settings-root",
            id="settings-root",
        ),
        # toast host
        Div("", id="set-toast", cls="set-toast", aria_live="polite"),
        Script(src="/static/js/settings.js", type="module"),
    )


def register(rt) -> None:
    @rt("/settings")
    def settings_page():
        # active_key="settings" is not a FEATURES nav pill, so no pill
        # highlights — correct for a global gear-reached page.
        return _Shell("settings", "Settings", body=_SettingsBody())
