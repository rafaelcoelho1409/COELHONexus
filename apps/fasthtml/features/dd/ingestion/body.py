"""Ingestion body — progress display + pages grid."""
from fasthtml.common import Button, Div, Span


def IngestionBody(slug: str | None):
    return Div(
        # Live progress display — hidden by default; pollRun() reveals it
        # only while an ingestion is actually in flight. Without this
        # `display:none`, recoverActiveRuns() on a plain visit with no
        # active run would leave the box visible showing stale "—".
        Div(
            Div(
                Span("—", id = "fw-progress-tier", cls = "fw-progress-tier"),
                Span("idle", id = "fw-progress-status", cls = "fw-progress-status"),
                cls = "fw-progress-head",
            ),
            Div(
                Div(cls = "fw-progress-fill", id = "fw-progress-fill"),
                cls = "fw-progress-bar indeterminate", id = "fw-progress-bar",
            ),
            Div(
                Span("", id = "fw-progress-counter"),
                Span(""),
                cls = "fw-progress-meta",
            ),
            Div("", id = "fw-progress-url", cls = "fw-progress-url"),
            Div(
                Div(
                    Div(id = "fw-progress-logos", cls = "fw-progress-logos"),
                    Span("", id = "fw-progress-framework",
                         cls = "fw-progress-framework"),
                    cls = "fw-progress-framework-box",
                ),
                Button("Cancel ingestion", id = "fw-cancel", cls = "btn-outline"),
                cls = "fw-progress-actions",
            ),
            id = "fw-progress-box", cls = "fw-progress", style = "display:none;",
        ),
        Div("", id = "fw-step2-summary", cls = "fw-pages-summary"),
        Div(
            Div(
                "Pick a framework from the Library dropdown above, or "
                "ingest a new one from the Catalog tab, to see its "
                "downloaded files." if not slug else "Loading…",
                cls = "fw-empty",
            ),
            id = "fw-step2-grid", cls = "fw-page-grid",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-2-panel",
    )
