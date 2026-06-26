"""Synth toolbar pieces — pill (status + elapsed) + actions (wipe/start).

Refine-budget dropdown was removed 2026-05-28: it was inert (the synth
graph is single-pass; the v2 self-refine loop never consumed it). When
`#fw-synth-budget` is absent, `startSynth` defaults the budget to '5'."""
from fasthtml.common import Button, Div, Span


def SynthPill():
    return Div(
        Span("Idle", cls = "fw-stage-pill-text", id = "fw-synth-pill-text"),
        # Total synth wall-clock (cumulative chapter wall + book_harmonize) —
        # updated live by synth.js and from /synth/{slug}/study/chapters
        # (study_total_wall_ms) on load/cached studies.
        Span("", cls = "fw-stage-elapsed", id = "fw-synth-elapsed",
             title = "Total Synth time"),
        cls = "fw-stage-pill", id = "fw-synth-pill", data_status = "idle",
    )


def SynthActions():
    # Planner's PlannerActions). A disabled button drops click events
    # entirely; gating happens in the inline body.py handler + the
    # module-side `startSynth` (both early-exit silently when the slug
    # isn't picked / no plan / a run is in flight).
    return Div(
        Button("Wipe synth", id = "fw-synth-wipe",
               cls = "btn-outline", disabled = True),
        Button("Start", id = "fw-synth-start",
               cls = "btn-primary"),
        cls = "fw-planner-head-actions",
    )
