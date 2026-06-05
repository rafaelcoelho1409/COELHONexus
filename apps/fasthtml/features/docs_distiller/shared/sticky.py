"""Catalog-only sticky Generate bar.

Two parallel labels — both visible when an ingestion is in flight:
  • `Selected: <tile the user clicked>`  (always present)
  • `Ingesting: <active framework>`      (only when activeRunId set)
The Ingesting label is hidden via CSS until ui.js refreshGenerateState
reveals it. Layout: flex row, both labels on the left, Start Ingestion
button on the right."""
from fasthtml.common import Button, Div, Span


def StickyBar():
    return Div(
        Span(
            Span("Selected:", id = "fw-selected-prefix",
                 cls = "fw-selected-prefix"),
            " ",
            Span("", id = "fw-selected-name", cls = "fw-selected-name"),
            id = "fw-selected-label", cls = "fw-selected-label",
        ),
        Span(
            Span("", id = "fw-ingesting-spinner",
                 cls = "fw-spinner fw-lib-spinner", aria_hidden = "true"),
            Span("Ingesting:", cls = "fw-ingesting-prefix"),
            " ",
            Span("", id = "fw-ingesting-name", cls = "fw-ingesting-name"),
            id = "fw-ingesting-label", cls = "fw-ingesting-label",
            style = "display:none;",
        ),
        Button("Start Ingestion", id = "fw-generate", cls = "btn-primary"),
        id = "fw-sticky-bar", cls = "fw-sticky-bar",
    )
