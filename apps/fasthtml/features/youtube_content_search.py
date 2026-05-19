"""YouTube Content Search — single-page Q&A over indexed YT transcripts.

Backend contract:
  POST /api/v1/youtube/runs  body={video_url, question}
  → {indexed, answer, citations, model, latency_s}

UX choices vs. docs_distiller (kept intentionally smaller for v0):
  - No multi-step wizard; one form + one answer block.
  - No SSE / live progress; the POST is ~6s, so a single sky-blue
    status banner ("Indexing transcript + answering…") fills the wait.
  - No library sidebar yet — each Ask is fully idempotent on the
    backend (index_video upserts by deterministic md5-UUID), so re-asking
    the same URL is fast and safe.

Client-side dynamic logic lives in static/js/youtube_content_search.js.
"""
from fasthtml.common import (
    Button, Div, Form, Input, P, Script, Span, Textarea,
)

from shell import _Shell


def _Step(n: int, label: str, active: bool = False):
    """One numbered step in the YCS wizard bar. Mirrors docs_distiller's
    `_Step` 1:1 — same DOM, same modifier classes, prefix-swapped CSS.
    JS toggles `.active` / `.completed` as the user moves through the
    Source → Ingest → Ask flow."""
    cls = "ycs-step active" if active else "ycs-step"
    return Div(
        Span(str(n), cls="ycs-step-circle"),
        Span(label, cls="ycs-step-label"),
        cls=cls,
        id=f"ycs-step-{n}",
        data_step=str(n),
    )


def _Steps():
    return Div(
        Div(
            _Step(1, "Source", active=True),
            Span(cls="ycs-step-connector"),
            _Step(2, "Ingest"),
            Span(cls="ycs-step-connector"),
            _Step(3, "Ask"),
            cls="ycs-stepper",
        ),
        cls="ycs-stepper-row",
    )


def _Form():
    return Form(
        Div(
            Span("YouTube video URL", cls="ycs-label"),
            Input(
                type="url",
                name="video_url",
                id="ycs-video-url",
                placeholder="https://www.youtube.com/watch?v=...",
                required=True,
                autocomplete="off",
                cls="ycs-input",
            ),
            cls="ycs-field",
        ),
        Div(
            Span("Your question", cls="ycs-label"),
            Textarea(
                "",
                name="question",
                id="ycs-question",
                placeholder="What is the speaker's main argument?",
                rows="3",
                required=True,
                cls="ycs-input ycs-textarea",
            ),
            cls="ycs-field",
        ),
        Div(
            Button("Ask", type="submit", id="ycs-submit", cls="btn-primary"),
            cls="ycs-actions",
        ),
        id="ycs-form",
        cls="ycs-form",
    )


def _StatusBanner():
    return Div(
        Span("", id="ycs-status-text", cls="ycs-status-text"),
        id="ycs-status",
        cls="ycs-status",
        style="display:none;",
    )


def _Indexed():
    return Div(
        "", id="ycs-indexed", cls="ycs-indexed", style="display:none;",
    )


def _AnswerCard():
    return Div(
        Div("Answer", cls="ycs-eyebrow"),
        Div("", id="ycs-answer-text", cls="ycs-answer-text"),
        Div(
            Span("", id="ycs-answer-meta", cls="ycs-answer-meta"),
            cls="ycs-answer-meta-row",
        ),
        id="ycs-answer",
        cls="ycs-answer",
        style="display:none;",
    )


def _Citations():
    return Div(
        Div("Sources", cls="ycs-eyebrow"),
        Div("", id="ycs-citations-list", cls="ycs-citations-list"),
        id="ycs-citations",
        cls="ycs-citations",
        style="display:none;",
    )


def _Body():
    return Div(
        _Steps(),
        P(
            "Paste a YouTube video URL and ask a question. The transcript is "
            "fetched, chunked, embedded, stored, and used to answer with "
            "citations — all in one request.",
            cls="ycs-intro",
        ),
        _Form(),
        _StatusBanner(),
        _Indexed(),
        _AnswerCard(),
        _Citations(),
        Script(src="/static/js/youtube_content_search.js", defer=True),
        cls="ycs-root",
    )


def register(rt) -> None:
    @rt("/youtube-content-search")
    def youtube_content_search():
        return _Shell(
            "youtube-content-search",
            "YouTube Content Search",
            body=_Body(),
        )
