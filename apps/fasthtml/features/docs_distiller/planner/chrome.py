"""Planner toolbar pieces — pill (status + elapsed) + actions (wipe/start)."""
from fasthtml.common import Button, Div, Span


def PlannerPill():
    return Div(
        Span("Idle", cls = "fw-stage-pill-text", id = "fw-planner-pill-text"),
        # Total planner wall-clock — updated live by planner.js (ticks while
        # running) and from GET /planner/{slug}/timing on load/cached runs.
        Span("", cls = "fw-stage-elapsed", id = "fw-planner-elapsed",
             title = "Total Planner time"),
        cls = "fw-stage-pill", id = "fw-planner-pill", data_status = "idle",
    )


def PlannerActions():
    return Div(
        Button("Wipe planner", id = "fw-planner-wipe",
               cls = "btn-outline", disabled = True,
               title = ("Delete this framework's planner cache "
                        "(MinIO embeddings + Postgres checkpoints "
                        "+ browser state)")),
        Button("Start Planner", id = "fw-planner-start",
               cls = "btn-primary", disabled = True),
        cls = "fw-planner-head-actions",
    )
