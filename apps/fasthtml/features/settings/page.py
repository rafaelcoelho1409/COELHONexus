"""Settings body — BYOK provider keys + free-model selection + optional
FastMCP source tool keys.

Server renders only the skeletons; the JS modules populate them:
  - settings.js            ← /api/v1/llm/settings/*   (LLM provider BYOK)
  - settings_tool_keys.js  ← /api/v1/rr/tool-credentials/*
                            (optional API keys for Research Radar source tools,
                             e.g. Semantic Scholar — unlocks higher rate limits)

Raw keys go browser → FastAPI on save and are NEVER returned. Responses
carry masked status only (has_key + source + last4)."""
from fasthtml.common import Button, Div, H3, Input, Label, P, Script, Span


def LLMEndpointCard():
    """The OpenAI-compatible endpoint the Docs Distiller / YCS apps call.
    COELHO LLM Rotator is always a separately-deployed service — Nexus never
    bundles one. Default = that rotator's dev-workflow address; point it at
    OpenAI, Anthropic, or a different rotator instance here. This field is
    the single source of truth for the endpoint at runtime — it always wins
    over the chart default. Populated + wired by settings_endpoint.js."""
    return Div(
        H3("LLM Endpoint", cls = "set-section-title"),
        P(
            "The OpenAI-compatible API the Docs Distiller uses. Leave as the "
            "default to use the dev-workflow COELHO LLM Rotator, or point it "
            "at OpenAI / Anthropic / a different rotator instance. The key is "
            "write-only — it never comes back to the browser.",
            cls = "settings-intro",
        ),
        Div(
            Label("Base URL", fr = "set-ep-url", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-ep-url", cls = "set-ep-input",
                placeholder = "http://coelho-llm-rotator-fastapi.coelho-llm-rotator-dev.svc.cluster.local:8000/api/v1/llm/openai/v1",
                autocomplete = "off", spellcheck = "false",
            ),
            Label("API key", fr = "set-ep-key", cls = "set-ep-label"),
            Input(
                type = "password", id = "set-ep-key", cls = "set-ep-input",
                placeholder = "(blank = no auth, e.g. the dev-workflow rotator)",
                autocomplete = "new-password",
            ),
            Label("Model", fr = "set-ep-model", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-ep-model", cls = "set-ep-input",
                placeholder = "auto", autocomplete = "off", spellcheck = "false",
            ),
            Div(
                Button("Save", id = "set-ep-save", type = "button",
                       cls = "set-btn set-btn-primary"),
                Button("Test", id = "set-ep-test", type = "button",
                       cls = "set-btn set-btn-ghost"),
                Span("", id = "set-ep-status", cls = "set-ep-status"),
                cls = "settings-actions",
            ),
            cls = "set-ep-fields",
            id = "settings-endpoint",
        ),
        cls = "settings-endpoint-card",
        id = "settings-endpoint-card",
    )


def SettingsBody():
    return Div(
        Div(
            LLMEndpointCard(),
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
        Script(src = "/static/js/settings_endpoint.js", type = "module"),
        Script(src = "/static/js/settings.js", type = "module"),
        Script(src = "/static/js/settings_tool_keys.js", type = "module"),
    )
