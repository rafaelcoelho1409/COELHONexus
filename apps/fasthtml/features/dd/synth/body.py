"""Synth body — empty state + 70/30 split: DAG canvas (left) + chapter checklist (right).

Chapter list lives in a narrow side panel (where a vertical list belongs)
instead of a full-width strip; clicking a chapter focuses its sub-graph
on the canvas (`_onStripCellClick`, already wired). Canvas + `#fw-chstrip`
both start display:none — JS reveals the graph when a framework is active
and the chapter panel only in study mode (≥2 chapters), so a non-study
run keeps the graph at full width (flex)."""
from fasthtml.common import Div, Span


def SynthBody(slug: str | None):
    empty_msg = (
        "Pick a framework from the library to view the synth pipeline."
        if not slug else
        "Loading synth state…"
    )
    return Div(
        Div(empty_msg, id = "fw-synth-empty", cls = "fw-stage-empty"),
        Div(
            Div(
                Div(id = "fw-synth-canvas", cls = "fw-stage-canvas"),
                id = "fw-synth-graph", cls = "fw-planner-graph",
            ),
            Div(
                Div(
                    Span("Chapters", cls = "fw-chstrip-title"),
                    Span(id = "fw-chstrip-counter", cls = "fw-chstrip-counter"),
                    cls = "fw-chstrip-head",
                ),
                Div(id = "fw-chstrip-cells", cls = "fw-chstrip-cells"),
                id = "fw-chstrip", cls = "fw-chstrip",
            ),
            cls = "fw-synth-split",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-4-panel",
    )
