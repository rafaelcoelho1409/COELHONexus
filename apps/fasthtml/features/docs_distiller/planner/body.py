"""Planner body — empty state + Cytoscape DAG canvas."""
from fasthtml.common import Div


def PlannerBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the planner pipeline."
        if not slug else
        "Loading planner state…"
    )
    # Header moved to the row-3 toolbar (PlannerPill + PlannerActions).
    # Title is redundant with the active stage tab; framework identity is
    # redundant with the Library picker — both dropped from the body.
    return Div(
        Div(empty_msg, id = "fw-planner-empty", cls = "fw-stage-empty"),
        Div(
            Div(id = "fw-planner-canvas", cls = "fw-stage-canvas"),
            id = "fw-planner-graph", cls = "fw-planner-graph",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-3-panel",
    )
