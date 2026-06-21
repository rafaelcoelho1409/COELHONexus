"""Pipeline toolbar — Planner + Synth controls + auto-chain checkbox
packed into a single compact row alongside the framework picker.

Per-stage Start/Stop/Wipe stays SEPARATE — auto-chain is opt-in per
the 2026-06 SOTA research. Buttons are relabeled to short forms
("Wipe" / "Start") and prefixed with the stage name as a label so the
row fits next to the framework picker without overflow."""
from fasthtml.common import Button, Div, Input, Label, Span


def _StageLabel(text: str):
    """Compact bold stage label that sits at the head of each cluster.
    Keeps the row scannable even when buttons are short ("Wipe" /
    "Start" instead of "Wipe planner" / "Start Planner")."""
    return Span(text, cls = "fw-pipeline-stage-label")


def _PlannerCluster():
    """Planner sub-row: label + Wipe + Start + Pill. Same DOM IDs
    (`fw-planner-wipe`, `fw-planner-start`, `fw-planner-pill*`,
    `fw-planner-elapsed`) the planner JS expects — only the visible
    labels are abbreviated for the pipeline toolbar."""
    return Div(
        _StageLabel("Planner"),
        Button("Wipe", id = "fw-planner-wipe",
               cls = "btn-outline", disabled = True,
               title = ("Delete this framework's planner cache "
                        "(MinIO embeddings + Postgres checkpoints "
                        "+ browser state)")),
        Button("Start", id = "fw-planner-start", cls = "btn-primary"),
        Div(
            Span("Idle", cls = "fw-stage-pill-text",
                 id = "fw-planner-pill-text"),
            Span("", cls = "fw-stage-elapsed", id = "fw-planner-elapsed",
                 title = "Total Planner time"),
            cls = "fw-stage-pill", id = "fw-planner-pill",
            data_status = "idle",
        ),
        cls = "fw-pipeline-stage-cluster",
        data_stage = "planner",
    )


def _SynthCluster():
    """Synth sub-row: label + Wipe + Start + Pill. Same DOM IDs the
    synth JS expects (`fw-synth-wipe`, `fw-synth-start`, etc.)."""
    return Div(
        _StageLabel("Synth"),
        Button("Wipe", id = "fw-synth-wipe",
               cls = "btn-outline", disabled = True),
        Button("Start", id = "fw-synth-start", cls = "btn-primary"),
        Div(
            Span("Idle", cls = "fw-stage-pill-text",
                 id = "fw-synth-pill-text"),
            Span("", cls = "fw-stage-elapsed", id = "fw-synth-elapsed",
                 title = "Total Synth time"),
            cls = "fw-stage-pill", id = "fw-synth-pill",
            data_status = "idle",
        ),
        cls = "fw-pipeline-stage-cluster",
        data_stage = "synth",
    )


def AutoChainToggle():
    """Opt-in checkbox: when Planner finishes successfully, auto-click
    `#fw-synth-start`. State persists in localStorage
    (`dd:pipeline:autochain`). Wired in body.py's inline script. Label
    shortened to "Auto-chain" for the compact row; full meaning still
    surfaces via tooltip."""
    return Label(
        Input(type = "checkbox", id = "fw-pipeline-autochain",
              cls = "fw-pipeline-autochain-cb"),
        Span("Auto-chain", cls = "fw-pipeline-autochain-label"),
        cls = "fw-pipeline-autochain",
        title = ("When Planner finishes successfully, Synth starts "
                 "automatically. Off by default — keep checked only "
                 "when you want a hands-off end-to-end run."),
    )


def PipelineTotalSummary():
    """Compact aggregate shown only when Planner + full Synth are complete."""
    return Div(
        Span("Pipeline total", cls = "fw-pipeline-total-label"),
        Span("—", id = "fw-pipeline-total-calls",
             cls = "fw-pipeline-total-metric",
             title = "Total LLM calls across Planner + Synth"),
        Span("—", id = "fw-pipeline-total-in",
             cls = "fw-pipeline-total-metric",
             title = "Total input tokens across Planner + Synth"),
        Span("—", id = "fw-pipeline-total-out",
             cls = "fw-pipeline-total-metric",
             title = "Total output tokens across Planner + Synth"),
        id = "fw-pipeline-total",
        cls = "fw-pipeline-total",
        title = ("Shown only when Planner and the full Synth study are "
                 "complete for this framework."),
        style = "display:none;",
    )


def PipelineActions():
    """Toolbar row 3 — Planner cluster → Synth cluster → auto-chain
    checkbox. The framework picker is added separately on the right
    (in StageToolbar)."""
    return Div(
        _PlannerCluster(),
        Div("→", cls = "fw-pipeline-arrow",
            title = "Synth runs after Planner"),
        _SynthCluster(),
        AutoChainToggle(),
        cls = "fw-pipeline-toolbar",
    )
