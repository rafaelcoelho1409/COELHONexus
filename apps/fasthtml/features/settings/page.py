"""Settings body — LLM endpoint + embedding preferences + optional FastMCP
source tool keys.

Server renders only the skeletons; the JS modules populate them:
  - settings_endpoint.js  ← /api/v1/llm/settings/endpoint    (LLM Endpoint)
  - settings_embedding.js ← /api/v1/llm/settings/embedding*  (Embedding)
  - settings_tool_keys.js ← /api/v1/rr/tool-credentials/*
                            (optional API keys for Research Radar source tools,
                             e.g. Semantic Scholar — unlocks higher rate limits)

2026-09-11: the old multi-provider chat registry (Groq / OpenRouter / Cerebras /
Mistral / Gemini / SambaNova / DeepSeek + NIM-for-chat) was removed from this
page — chat routing is now always the externally-deployed COELHO LLM Rotator,
configured via the single LLM Endpoint field below.

2026-09-12: the standalone "NVIDIA API Key" card (added when YCS embeddings/
reranking read NIM directly) is removed too — replaced by the Embedding
card below, an independent OpenAI-compatible endpoint (own URL/key/model,
same shape as LLM Endpoint) that can point at COELHO LLM Rotator or any
other embedding service, not tied to chat's connection. No language-
preference field — YCS ingests videos in whatever language they're
actually in, per-run, so a single Settings-page language wouldn't make
sense; a language hint stays available to internal callers programmatically
(see `domains/llm/embeddings/service.py::embed_probe_async`) if ever
needed. NVIDIA NIM's key remains settable via the generic
`/providers/nim/key` endpoint (unchanged, just no longer surfaced as its
own card here) for the rare caller that still wants it directly (DD's
local-embedding fallback chain).

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


def EmbeddingCard():
    """The OpenAI-compatible embedding endpoint YCS calls — independent
    connection from the LLM Endpoint above, same shape and same flexibility:
    point it at COELHO LLM Rotator (its Embedding Curator resolves the
    actual model) or any other OpenAI-compatible embedding service. Test
    fires one real embeddings call and reports back the resolved model +
    dimension + latency — the only way to actually confirm the endpoint's
    /v1/embeddings surface works. Populated + wired by
    settings_embedding.js."""
    return Div(
        H3("Embedding", cls = "set-section-title"),
        Div(
            Label("Base URL", fr = "set-emb-url", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-emb-url", cls = "set-ep-input",
                autocomplete = "off", spellcheck = "false",
            ),
            Label("API key", fr = "set-emb-key", cls = "set-ep-label"),
            Div(
                Input(
                    type = "password", id = "set-emb-key", cls = "set-ep-input",
                    autocomplete = "new-password",
                ),
                Span("", id = "set-emb-key-status", cls = "set-pill set-pill-none"),
                cls = "set-ep-key-row",
            ),
            Label("Model", fr = "set-emb-model", cls = "set-ep-label"),
            Input(
                type = "text", id = "set-emb-model", cls = "set-ep-input",
                autocomplete = "off", spellcheck = "false",
            ),
            Div(
                Button("Save", id = "set-emb-save", type = "button",
                       cls = "set-btn set-btn-primary"),
                Button("Test", id = "set-emb-test", type = "button",
                       cls = "set-btn set-btn-ghost"),
                Span("", id = "set-emb-status", cls = "set-ep-status"),
                cls = "settings-actions",
            ),
            cls = "set-ep-fields",
            id = "settings-embedding",
        ),
        cls = "settings-endpoint-card",
        id = "settings-embedding-card",
    )


def SettingsBody():
    return Div(
        Div(
            LLMEndpointCard(),
            EmbeddingCard(),
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
        Script(src = "/static/js/settings_embedding.js", type = "module"),
        Script(src = "/static/js/settings_tool_keys.js", type = "module"),
    )
