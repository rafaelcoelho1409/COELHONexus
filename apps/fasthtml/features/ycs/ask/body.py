"""Step 3 · Ask — Adaptive RAG chat over the indexed transcripts.

Two regions, populated by `ask.js`:

(a) Composer — channel multi-select + textarea + Send / Stop. Submit
    kicks off an SSE request to `/api/v1/ycs/agents/search/stream`.
    The mode pill (Auto / Fast / Standard / Deep) and the LLM-config
    dropdown both live in row 3 of the topbar (see `ask/chrome.py`
    + `shared/toolbar.py`) — the form IDs there match what
    `ask.js` already binds.

(b) Conversation — server-rendered shell; ask.js fills it as SSE events
    arrive: node-by-node progress (stage pills + DEEP cards), streaming
    generation, citations panel.

Fresh code — the deprecated repo had no FastHTML, so this UI is
modeled after `features/dd/`'s pattern + the deprecated agent endpoints'
JSON shapes."""
from __future__ import annotations

from fasthtml.common import (
    Button, Div, Form, H2, Label, Option, P, Select, Span, Textarea,
)


def _Composer():
    return Form(
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
            Button("Stop", type = "button",
                   id = "ycs-ask-stop",
                   cls = "btn-secondary",
                   style = "display:none;",
                   title = "Abort the current request"),
            Button("Send", type = "submit", cls = "btn-primary"),
            cls = "ycs-form-actions",
        ),
        id = "ycs-ask-form",
    )


_EXAMPLE_QUESTIONS: list[str] = [
    "What are the main themes across all videos?",
    "Compare the recommendations made by each channel.",
    "What contradictions appear between sources?",
]


def _EmptyState():
    """First-load welcome hint with example questions. ask.js hides
    this `<div>` the moment the conversation panel gets any content
    (a history rehydrate, a streamed answer, or an error)."""
    chips = [
        Button(
            q,
            type = "button",
            cls  = "ycs-ask-example-chip",
            data_question = q,
        )
        for q in _EXAMPLE_QUESTIONS
    ]
    return Div(
        Span("Try asking", cls = "ycs-ask-empty-label"),
        Div(*chips, cls = "ycs-ask-empty-chips"),
        cls = "ycs-ask-empty",
        id  = "ycs-ask-empty",
    )


def _Conversation():
    """Conversation panel — historical Q+A turns above, live-stream
    target below.

    `#ycs-ask-history` accumulates past turns; `#ycs-ask-output` is the
    landing zone for the currently-streaming generation. On each Send,
    `ask.js` first snapshots the prior turn into history, then resets
    the output region for the next answer.

    The thread id + New thread button now live in the row-3 toolbar
    (`ask/chrome.py::AskThreadBar`)."""
    return Div(
        _EmptyState(),
        Div("", id = "ycs-ask-history", cls = "ycs-ask-history"),
        Div(
            Div("Retrieve",   cls = "ycs-step-circle", data_stage = "retrieve"),
            Div("Grade",      cls = "ycs-step-circle", data_stage = "grade"),
            Div("Generate",   cls = "ycs-step-circle", data_stage = "generate"),
            Div("Verify",     cls = "ycs-step-circle", data_stage = "verify"),
            cls = "ycs-ask-stages",
            id  = "ycs-ask-stages",
            style = "display:none;",
        ),
        # DEEP-mode research panel. Hidden by default; ask.js shows it
        # on `plan_research` and fills in one card per sub-question.
        # `#ycs-ask-deep-banner` carries the synthesize/critic status.
        Div(
            Div("", id = "ycs-ask-deep-banner", cls = "ycs-ask-deep-banner"),
            Div("", id = "ycs-ask-deep-cards",  cls = "ycs-ask-deep-cards"),
            cls   = "ycs-ask-deep",
            id    = "ycs-ask-deep",
            style = "display:none;",
        ),
        Div(
            Div("", id = "ycs-ask-answer", cls = "ycs-ask-answer"),
            Div("", id = "ycs-ask-followups", cls = "ycs-ask-followups"),
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
        _Composer(),
        _Conversation(),
    )
