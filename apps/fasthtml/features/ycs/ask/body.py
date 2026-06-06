"""Step 3 · Ask — Adaptive RAG chat over the indexed transcripts.

Three regions, populated by `ask.js`:

(a) LLM config form (collapsible) — POSTs to `/api/v1/ycs/agents/config`.
    Persists provider/model/temperature/base_url/api_key as JSON in Redis
    so the agentic-RAG nodes pick up user-supplied overrides.

(b) Composer — mode pill (Auto / Fast / Standard / Deep) + channel
    multi-select + textarea + Send. Submit kicks off an SSE request to
    `/api/v1/ycs/agents/search/stream`.

(c) Conversation — server-rendered shell; ask.js fills it as SSE events
    arrive: node-by-node progress, streaming generation, citations panel.

Fresh code — the deprecated repo had no FastHTML, so this UI is
modeled after `features/dd/`'s pattern + the deprecated agent endpoints'
JSON shapes."""
from __future__ import annotations

from fasthtml.common import (
    Button, Div, Form, H2, Input, Label, Option, P, Select, Span, Textarea,
)


_MODES: list[tuple[str, str]] = [
    ("",         "Auto"),
    ("fast",     "Fast"),
    ("standard", "Standard"),
    ("deep",     "Deep"),
]


def _ModePill():
    pills = [
        Button(
            label,
            type   = "button",
            cls    = "ycs-mode-pill active" if key == "" else "ycs-mode-pill",
            data_mode = key,
        )
        for key, label in _MODES
    ]
    return Div(
        Span("Mode", cls = "ycs-mode-label"),
        Div(*pills, cls = "ycs-mode-row", role = "tablist"),
    )


def _LLMConfigForm():
    """Override the YCS LLM at runtime. Mirror of deprecated `LLMConfig`
    schema (`schemas/youtube/inputs.py:L16-22`). Persisted to Redis JSON
    so it survives FastAPI restarts."""
    return Div(
        Button(
            Span("LLM configuration", cls = "ycs-filters-toggle-label"),
            Span("▾", cls = "ycs-filters-toggle-chevron"),
            type = "button",
            cls = "ycs-filters-toggle",
            data_target = "ycs-llm-body",
            aria_expanded = "false",
        ),
        Form(
            Div(
                Div(
                    Label("Provider", cls = "ycs-filter-label",
                          for_ = "ycs-llm-provider"),
                    Input(type = "text", name = "provider",
                          id = "ycs-llm-provider", value = "NVIDIA",
                          cls = "ycs-filter-input"),
                    cls = "ycs-filter-field",
                ),
                Div(
                    Label("Model", cls = "ycs-filter-label",
                          for_ = "ycs-llm-model"),
                    Input(type = "text", name = "model",
                          id = "ycs-llm-model",
                          placeholder = "meta/llama-3.3-70b-instruct",
                          cls = "ycs-filter-input"),
                    cls = "ycs-filter-field",
                ),
                Div(
                    Label("Temperature", cls = "ycs-filter-label",
                          for_ = "ycs-llm-temp"),
                    Input(type = "number", min = "0", max = "2",
                          step = "0.05", name = "temperature",
                          id = "ycs-llm-temp", placeholder = "0.0",
                          cls = "ycs-filter-input"),
                    cls = "ycs-filter-field",
                ),
                Div(
                    Label("Base URL", cls = "ycs-filter-label",
                          for_ = "ycs-llm-base"),
                    Input(type = "text", name = "base_url",
                          id = "ycs-llm-base",
                          placeholder = "https://integrate.api.nvidia.com/v1",
                          cls = "ycs-filter-input"),
                    cls = "ycs-filter-field",
                ),
                Div(
                    Label("API key (write-only)", cls = "ycs-filter-label",
                          for_ = "ycs-llm-key"),
                    Input(type = "password", name = "api_key",
                          id = "ycs-llm-key", autocomplete = "off",
                          placeholder = "nvapi-…",
                          cls = "ycs-filter-input"),
                    cls = "ycs-filter-field",
                ),
                cls = "ycs-llm-fields",
                style = ("grid-template-columns: repeat(2, 1fr); "
                         "gap: 14px 18px;"),
            ),
            Div(
                Span("", id = "ycs-llm-status", cls = "ycs-search-status"),
                Button("Save configuration", type = "submit",
                       cls = "btn-primary"),
                cls = "ycs-form-actions",
            ),
            id = "ycs-llm-form",
        ),
        cls = "ycs-filters",
        id  = "ycs-llm-panel",
    )


def _Composer():
    return Form(
        _ModePill(),
        Div(
            Label("Scope (optional)", cls = "ycs-filter-label",
                  for_ = "ycs-ask-channels"),
            Select(
                Option("All channels (auto)", value = ""),
                id        = "ycs-ask-channels",
                cls       = "ycs-filter-select",
                multiple  = True,
                size      = "4",
                title     = "Hold Ctrl/⌘ to pick multiple channels",
            ),
            cls = "ycs-filter-field",
        ),
        Textarea(
            name        = "question",
            id          = "ycs-ask-input",
            rows        = "4",
            placeholder = (
                "Ask anything about the indexed videos…\n"
                "Auto mode picks fast / standard / deep based on the query."
            ),
            cls = "ycs-input ycs-textarea",
            required = True,
        ),
        Div(
            Span("", id = "ycs-ask-status", cls = "ycs-search-status"),
            Button("Send", type = "submit", cls = "btn-primary"),
            cls = "ycs-form-actions",
        ),
        id = "ycs-ask-form",
    )


def _Conversation():
    return Div(
        Div(
            Div("Retrieve",   cls = "ycs-step-circle", data_stage = "retrieve"),
            Div("Grade",      cls = "ycs-step-circle", data_stage = "grade"),
            Div("Generate",   cls = "ycs-step-circle", data_stage = "generate"),
            Div("Verify",     cls = "ycs-step-circle", data_stage = "verify"),
            cls = "ycs-ask-stages",
            id  = "ycs-ask-stages",
            style = "display:none;",
        ),
        Div(
            Div("", id = "ycs-ask-answer", cls = "ycs-ask-answer"),
            Div("", id = "ycs-ask-citations", cls = "ycs-ask-citations"),
            cls = "ycs-ask-output",
            id  = "ycs-ask-output",
        ),
    )


def AskBody(slug: str | None):
    return Div(
        H2("Ask", style = "margin: 0 0 8px 0; font-weight: 500;"),
        P(
            "Chat over the indexed transcripts. The Adaptive RAG graph "
            "auto-routes simple questions through a fast path, factual "
            "questions through full retrieval + grading, and analytical "
            "questions through multi-agent research.",
            cls = "ycs-intro",
        ),
        _LLMConfigForm(),
        _Composer(),
        _Conversation(),
    )
