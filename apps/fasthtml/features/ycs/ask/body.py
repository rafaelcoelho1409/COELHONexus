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
    Button, Div, Form, H3, P, Span, Textarea,
)


def _Composer():
    return Form(
        Textarea(
            name        = "question",
            id          = "ycs-ask-input",
            rows        = "1",
            placeholder = "Ask anything about the indexed videos…",
            cls = "ycs-input ycs-textarea",
            required = True,
        ),
        Div(
            Span("", id = "ycs-ask-status", cls = "ycs-search-status"),
            Button("Stop",
                   type  = "button",
                   id    = "ycs-ask-stop",
                   cls   = "btn-primary ycs-ask-stop",
                   style = "display:none;",
                   title = "Stop the current response"),
            Button("Send",
                   type  = "submit",
                   id    = "ycs-ask-send",
                   cls   = "btn-primary",
                   title = "Send (Enter)"),
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
    """First-load welcome zone — headline + brief explainer + example
    prompts. `ask.js` hides this `<div>` the moment the conversation
    panel gets any content (a history rehydrate, a streamed answer, or
    an error). The old `<H2>Ask</H2>` page title + intro paragraph were
    folded in here: SOTA chat UIs (ChatGPT, Claude) don't pin a title
    above the message column — the empty state IS the title surface."""
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
        H3("Ask the indexed transcripts",
           cls = "ycs-ask-empty-title"),
        P(
            "Adaptive RAG: simple lookups go through a fast path, "
            "factual questions through full retrieval + grading, and "
            "analytical questions through multi-agent research.",
            cls = "ycs-ask-empty-intro",
        ),
        Span("Try asking", cls = "ycs-ask-empty-label"),
        Div(*chips, cls = "ycs-ask-empty-chips"),
        cls = "ycs-ask-empty",
        id  = "ycs-ask-empty",
    )


def _SourcesRail():
    return Div(
        Div(
            Span("Sources", cls = "ycs-ask-rail-label"),
            Span("0",
                 id  = "ycs-ask-rail-count",
                 cls = "ycs-ask-rail-count"),
            cls = "ycs-ask-rail-head",
        ),
        Div("",
            id  = "ycs-ask-rail-list",
            cls = "ycs-ask-rail-list"),
        cls = "ycs-ask-rail",
        id  = "ycs-ask-rail",
    )


def AskBody(slug: str | None):
    """Continuous chat feed + sticky sources rail + ChatGPT-style
    pinned composer.

    2-column layout (Perplexity / Claude shape, mid-2026): the LEFT
    column is a flex stack — empty-state (hidden once a turn lands) +
    scrolling conversation feed + a sticky-bottom DOCK that wraps the
    composer card. The dock has its own opaque page-bg background
    extending edge-to-edge so scrolled-up text NEVER shows through
    around the composer's rounded corners or below it. The RIGHT
    column is the sticky sources rail.

    Thread id, Scope, Mode pills, and the LLM Configuration dropdown
    all live in the row-3 toolbar (`ask/chrome.py`); this body is just
    the conversation surface."""
    return Div(
        Div(
            _EmptyState(),
            Div("",
                id  = "ycs-ask-conversation",
                cls = "ycs-ask-conversation"),
            Div(
                _Composer(),
                cls = "ycs-ask-composer-dock",
            ),
            cls = "ycs-ask-main",
        ),
        _SourcesRail(),
        cls = "ycs-ask-layout",
    )
