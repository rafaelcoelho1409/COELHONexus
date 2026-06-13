"""RRBody — Research Radar scan page.

Layout:
  ┌──────────────────────────────────────────────┐
  │  Scan form: topic · verticals · top_n        │
  ├──────────────────────────────────────────────┤
  │  Live status strip (phase + last message)    │
  ├──────────────────────────────────────────────┤
  │  Digest cards (rendered after status=done)   │
  └──────────────────────────────────────────────┘
"""
from fasthtml.common import (
    H2, H3, Button, Div, Form, Input, Label, P, Script, Span,
)


def _ScanForm():
    return Form(
        Div(
            Label("Topic", For="topic", cls = "rr-label"),
            Input(
                id          = "topic",
                name        = "topic",
                placeholder = "e.g. deep agents",
                value       = "deep agents",
                required    = True,
                cls         = "rr-input",
            ),
            cls = "rr-field",
        ),
        Div(
            Label("Verticals (comma-separated)", For="verticals", cls = "rr-label"),
            Input(
                id          = "verticals",
                name        = "verticals",
                placeholder = "cs.LG, cs.AI",
                value       = "cs.LG, cs.AI",
                cls         = "rr-input",
            ),
            cls = "rr-field",
        ),
        Div(
            Label("Top N", For="top_n", cls = "rr-label"),
            Input(
                id    = "top_n",
                name  = "top_n",
                type  = "number",
                value = "8",
                min   = "4",
                max   = "30",
                cls   = "rr-input rr-input-narrow",
            ),
            cls = "rr-field",
        ),
        Div(
            Button("Start Scan", type = "submit", cls = "rr-submit"),
            cls = "rr-actions",
        ),
        id  = "rr-scan-form",
        cls = "rr-form",
    )


def _StatusStrip():
    return Div(
        Div(
            Span("●", cls = "rr-status-dot"),
            Span("Idle — fill the form and click Start Scan", cls = "rr-status-text", id = "rr-status-text"),
            cls = "rr-status-line",
        ),
        Div(id = "rr-status-detail", cls = "rr-status-detail"),
        id  = "rr-status",
        cls = "rr-status-strip",
    )


def _DigestArea():
    return Div(
        H3("Digest", cls = "rr-digest-title"),
        P(
            "Findings appear here once the scan completes.",
            id  = "rr-digest-empty",
            cls = "rr-digest-empty",
        ),
        Div(id = "rr-digest-items", cls = "rr-digest-items"),
        cls = "rr-digest",
    )


def RRBody():
    return Div(
        Div(
            H2("Start a scan", cls = "rr-section-title"),
            _ScanForm(),
            cls = "rr-card rr-card-form",
        ),
        Div(
            _StatusStrip(),
            cls = "rr-card rr-card-status",
        ),
        Div(
            _DigestArea(),
            cls = "rr-card rr-card-digest",
        ),
        Script(src = "/static/js/rr/main.js", type = "module"),
        cls = "rr-page",
    )
