"""Settings body — BYOK provider keys + free-model selection + optional
FastMCP source tool keys.

Server renders only the skeletons; the JS modules populate them:
  - settings.js            ← /api/v1/llm/settings/*   (LLM provider BYOK)
  - settings_tool_keys.js  ← /api/v1/rr/tool-credentials/*
                            (optional API keys for Research Radar source tools,
                             e.g. Semantic Scholar — unlocks higher rate limits)

Raw keys go browser → FastAPI on save and are NEVER returned. Responses
carry masked status only (has_key + source + last4)."""
from fasthtml.common import Button, Div, H3, P, Script, Span


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
            # FastMCP source tool keys (Research Radar) — separate skeleton +
            # JS module. Hidden by default until JS populates; if the catalog
            # is empty the section quietly stays collapsed.
            Div(
                H3("Source Tool Keys", cls = "set-section-title"),
                P(
                    "Optional API keys for Research Radar source tools. These "
                    "unlock higher rate limits or extra features on third-party "
                    "data sources. Tools work without keys, just slower.",
                    cls = "settings-intro",
                ),
                Div(
                    Div("Loading tool keys…", cls = "set-loading"),
                    id = "settings-tool-keys-list",
                    cls = "settings-tool-keys-list",
                ),
                cls = "settings-tool-keys",
                id = "settings-tool-keys",
            ),
            cls = "settings-root",
            id = "settings-root",
        ),
        Div("", id = "set-toast", cls = "set-toast", aria_live = "polite"),
        Script(src = "/static/js/settings.js", type = "module"),
        Script(src = "/static/js/settings_tool_keys.js", type = "module"),
    )
