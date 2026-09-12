"""Settings body — LLM endpoint + standalone NVIDIA key + optional
FastMCP source tool keys.

Server renders only the skeletons; the JS modules populate them:
  - settings_endpoint.js  ← /api/v1/llm/settings/endpoint   (LLM Endpoint)
  - settings_nim_key.js   ← /api/v1/llm/settings/providers/nim/*
                            (standalone NVIDIA key — YCS embeddings + reranking,
                             unrelated to chat routing)
  - settings_tool_keys.js ← /api/v1/rr/tool-credentials/*
                            (optional API keys for Research Radar source tools,
                             e.g. Semantic Scholar — unlocks higher rate limits)

2026-09-11: the old multi-provider chat registry (Groq / OpenRouter / Cerebras /
Mistral / Gemini / SambaNova / DeepSeek + NIM-for-chat) was removed from this
page — chat routing is now always the externally-deployed COELHO LLM Rotator,
configured via the single LLM Endpoint field below. NVIDIA NIM's key survives
as its own field because YCS embeddings/reranking reads it directly, a
separate concern from chat routing.

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
        Div(
            Label("Base URL", fr = "set-ep-url", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-ep-url", cls = "set-ep-input",
                autocomplete = "off", spellcheck = "false",
            ),
            Label("API key", fr = "set-ep-key", cls = "set-ep-label"),
            Div(
                Input(
                    type = "password", id = "set-ep-key", cls = "set-ep-input",
                    autocomplete = "new-password",
                ),
                # Raw keys are write-only (never returned) — this pill is how
                # a previously-saved key's presence still "shows up" on the
                # field without the field itself ever holding the real value.
                # Same pattern as Source Tool Keys' status pill.
                Span("", id = "set-ep-key-status", cls = "set-pill set-pill-none"),
                cls = "set-ep-key-row",
            ),
            Label("Model", fr = "set-ep-model", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-ep-model", cls = "set-ep-input",
                autocomplete = "off", spellcheck = "false",
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


def NvidiaKeyCard():
    """Standalone NVIDIA NIM API key. Unrelated to chat routing (that's the
    LLM Endpoint above) — this is what YCS embeddings + reranking reads
    directly. Reuses the existing generic /providers/nim/{key,test} endpoints
    (pid="nim") that used to back the removed multi-provider registry UI.
    Populated + wired by settings_nim_key.js."""
    return Div(
        H3("NVIDIA API Key", cls = "set-section-title"),
        Div(
            Label("API key", fr = "set-nim-key", cls = "set-ep-label"),
            Div(
                Input(
                    type = "password", id = "set-nim-key", cls = "set-ep-input",
                    autocomplete = "new-password",
                ),
                Span("", id = "set-nim-key-status", cls = "set-pill set-pill-none"),
                cls = "set-ep-key-row",
            ),
            Div(
                Button("Save", id = "set-nim-save", type = "button",
                       cls = "set-btn set-btn-primary"),
                Button("Test", id = "set-nim-test", type = "button",
                       cls = "set-btn set-btn-ghost"),
                Button("Remove", id = "set-nim-remove", type = "button",
                       cls = "set-btn set-btn-danger"),
                Span("", id = "set-nim-status", cls = "set-ep-status"),
                cls = "settings-actions",
            ),
            cls = "set-ep-fields",
            id = "settings-nim-key",
        ),
        cls = "settings-endpoint-card",
        id = "settings-nim-key-card",
    )


def SettingsBody():
    return Div(
        Div(
            LLMEndpointCard(),
            NvidiaKeyCard(),
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
        Script(src = "/static/js/settings_nim_key.js", type = "module"),
        Script(src = "/static/js/settings_tool_keys.js", type = "module"),
    )
