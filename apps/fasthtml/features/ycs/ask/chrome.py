"""Row-3 chrome for the Ask stage — mode pills + thread bar (left) +
LLM picker (right).

Three pieces:

  * `AskModeTabs(active="")` — Auto / Fast / Standard / Deep pills. The
    DOM contract `[data-mode]` + `.ycs-mode-pill` is preserved so
    `ask.js` keeps binding without changes; the additional
    `.dd-substage` class gives them the same row-3 visual language as
    the Source-stage tabs (Search | Videos | Playlist | Channel).

  * `AskThreadBar()` — current thread id badge + New thread button.
    DOM ids (`#ycs-ask-thread-id`, `#ycs-ask-new-thread`) match what
    `ask.js` already binds; only their position moved.

  * `AskLLMTrigger()` — `.dd-catfilter` clone hosting the full
    `LLMConfig` form in a popover. Form/field ids match the ones
    `ask.js` already targets (`#ycs-llm-form`, `#ycs-llm-provider`, …)
    so wiring is unchanged; only the open/close handler in `ask.js`
    is rewritten to use the `.dd-catfilter` `.open` pattern."""
from __future__ import annotations

from fasthtml.common import (
    Button, Div, Form, Input, Label, Nav, Span,
)


_MODES: list[tuple[str, str]] = [
    ("",         "Auto"),
    ("fast",     "Fast"),
    ("standard", "Standard"),
    ("deep",     "Deep"),
]


def AskModeTabs(active: str = ""):
    """Auto | Fast | Standard | Deep — row-3 left cluster. The Auto pill
    (data-mode="") is the default `active` so first-load matches the
    server-side `force_mode = None` behaviour."""
    pills = [
        Button(
            label,
            type      = "button",
            cls       = ("ycs-mode-pill dd-substage active"
                         if key == active else
                         "ycs-mode-pill dd-substage"),
            data_mode = key,
        )
        for key, label in _MODES
    ]
    return Nav(
        *pills,
        cls         = "dd-substage-nav ycs-ask-modes",
        role        = "tablist",
        aria_label  = "Ask mode",
    )


def AskThreadBar():
    """Thread picker — row-3 right cluster.

    `.dd-catfilter` dropdown: trigger shows the current thread id (set
    by `ask.js` on boot from `localStorage`), popover hosts:
      1. A `+ New thread` row at the top — preserves the existing
         `#ycs-ask-new-thread` id so the create handler keeps working.
      2. A list (`#ycs-ask-thread-list`) populated on open by
         `loadThreadList()` from `GET /agents/threads`. Each row
         carries a `data-thread-id` so a single delegated click handler
         routes to `switchThread(id)`."""
    return Div(
        Button(
            Span("Thread:", cls = "dd-catfilter-prefix"),
            Span(
                "",
                cls   = "dd-catfilter-label ycs-ask-thread-id",
                id    = "ycs-ask-thread-id",
                title = "Conversation memory key (Postgres)",
            ),
            Span("▾", cls = "dd-catfilter-chevron"),
            type       = "button",
            cls        = "dd-catfilter-trigger",
            id         = "ycs-ask-thread-trigger",
            aria_label = "Switch thread",
        ),
        Div(
            Button(
                "+ New thread",
                type  = "button",
                id    = "ycs-ask-new-thread",
                cls   = "ycs-ask-thread-new",
                title = "Start a fresh conversation",
            ),
            Div(
                "",
                id  = "ycs-ask-thread-list",
                cls = "ycs-ask-thread-list",
            ),
            cls = "dd-catfilter-popover ycs-ask-thread-popover",
        ),
        cls = "dd-catfilter ycs-ask-thread",
        id  = "ycs-ask-thread",
    )


def AskLLMTrigger():
    """LLM configuration dropdown — row-3 right cluster.

    Visual language: `.dd-catfilter` (the same trigger + popover idiom
    the Ingestion stage uses for facet filters). Inside the popover
    sits the full `LLMConfig` form — same ids as the old body-resident
    form, so `ask.js` keeps targeting them by id."""
    fields = Div(
        Div(
            Label("Provider",
                  cls = "ycs-filter-label", for_ = "ycs-llm-provider"),
            Input(type = "text", name = "provider",
                  id = "ycs-llm-provider", value = "NVIDIA",
                  cls = "ycs-filter-input"),
            cls = "ycs-filter-field",
        ),
        Div(
            Label("Model",
                  cls = "ycs-filter-label", for_ = "ycs-llm-model"),
            Input(type = "text", name = "model",
                  id = "ycs-llm-model",
                  placeholder = "meta/llama-3.3-70b-instruct",
                  cls = "ycs-filter-input"),
            cls = "ycs-filter-field",
        ),
        Div(
            Label("Temperature",
                  cls = "ycs-filter-label", for_ = "ycs-llm-temp"),
            Input(type = "number", min = "0", max = "2",
                  step = "0.05", name = "temperature",
                  id = "ycs-llm-temp", placeholder = "0.0",
                  cls = "ycs-filter-input"),
            cls = "ycs-filter-field",
        ),
        Div(
            Label("Base URL",
                  cls = "ycs-filter-label", for_ = "ycs-llm-base"),
            Input(type = "text", name = "base_url",
                  id = "ycs-llm-base",
                  placeholder = "https://integrate.api.nvidia.com/v1",
                  cls = "ycs-filter-input"),
            cls = "ycs-filter-field",
        ),
        Div(
            Label("API key (write-only)",
                  cls = "ycs-filter-label", for_ = "ycs-llm-key"),
            Input(type = "password", name = "api_key",
                  id = "ycs-llm-key", autocomplete = "off",
                  placeholder = "nvapi-…",
                  cls = "ycs-filter-input"),
            cls = "ycs-filter-field",
        ),
        cls   = "ycs-llm-fields",
        style = "grid-template-columns: repeat(2, 1fr); gap: 14px 18px;",
    )
    actions = Div(
        Span("", id = "ycs-llm-status", cls = "ycs-search-status"),
        Button("Test", type = "button",
               id = "ycs-llm-test",
               cls = "btn-secondary",
               title = ("Fire one ping round-trip against the form "
                        "values BEFORE saving — validates the key.")),
        Button("Save configuration", type = "submit",
               cls = "btn-primary"),
        cls = "ycs-form-actions",
    )
    return Div(
        Button(
            Span("LLM:", cls = "dd-catfilter-prefix"),
            Span("Configuration",
                 cls = "dd-catfilter-label",
                 id  = "ycs-ask-llm-label"),
            Span("▾", cls = "dd-catfilter-chevron"),
            type       = "button",
            cls        = "dd-catfilter-trigger",
            id         = "ycs-ask-llm-trigger",
            aria_label = "LLM configuration",
        ),
        Div(
            Form(
                fields,
                actions,
                id = "ycs-llm-form",
            ),
            cls = "dd-catfilter-popover ycs-ask-llm-popover",
        ),
        cls = "dd-catfilter ycs-ask-llm",
        id  = "ycs-ask-llm",
    )
