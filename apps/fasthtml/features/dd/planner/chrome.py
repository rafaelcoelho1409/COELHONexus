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
    # The Start button is NOT server-rendered as disabled. Previously it
    # was, on the assumption that JS `refreshPlannerStartState()` would
    # un-disable it after gates pass. Empirically that path is fragile:
    # if `initPlanner()` errors anywhere between the dynamic import and
    # the refresh call (silent — main.js catches it as `[init] stage
    # planner failed`), the button stays permanently disabled and a
    # click produces no event at all. Letting the click reach JS makes
    # the gating happen in `startPlanner` itself, which is the single
    # source of truth anyway (it returns silently when slug is missing
    # or a run is already in flight). The Wipe button stays disabled —
    # that's a destructive action where a fail-closed default matters.
    return Div(
        Button("Wipe planner", id = "fw-planner-wipe",
               cls = "btn-outline", disabled = True,
               title = ("Delete this framework's planner cache "
                        "(MinIO embeddings + Postgres checkpoints "
                        "+ browser state)")),
        Button("Start Planner", id = "fw-planner-start",
               cls = "btn-primary"),
        cls = "fw-planner-head-actions",
    )
