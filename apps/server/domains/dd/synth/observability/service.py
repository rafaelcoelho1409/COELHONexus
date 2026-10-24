"""Synth observability — the `@traced` node decorator (one span per synth node, with the chapter id for the per-chapter Gantt) and the pipeline metric recorders; instruments live in infra.opentelemetry.entities."""
from __future__ import annotations
import domains, infra


def _llm_context(state: dict, node_name: str):
    """Attribute the LLM calls inside this node to (synth, thread, node)."""
    return domains.settings.llm_counter.service.llm_context(
        ns = "dd", run_id = state.get("thread_id") or "", node_id = node_name, stage = "synth",
    )


traced = infra.opentelemetry.service.node_tracer(
    prefix      = "synth",
    workflow    = "dd_synth",
    stage       = "synth",
    span_prefix = "dd.synth.node",
    state_attrs = lambda state: {
        "thread_id":      state.get("thread_id") or "",
        "framework_slug": state.get("framework_slug") or "",
        "chapter_id":     state.get("chapter_id") or "",
    },
    context     = _llm_context,
)


def record_chapter_outcome(
    *,
    outcome: str,
    framework: str,
    chapter_number: int,
    pinned_model: str | None = None,
    duration_s: float | None = None,
    iterations: int | None = None,
) -> None:
    """Outcome + duration + iters for one chapter's synth lifecycle."""
    attrs = {
        "framework":      framework,
        "chapter_number": str(chapter_number),
        "outcome":        outcome,
    }
    if pinned_model:
        attrs["pinned_model"] = pinned_model
    try:
        if (inst := infra.opentelemetry.service.get_instrument("chapter_outcome")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument("chapter_synth_duration")
        ) is not None:
            inst.record(duration_s, attributes = attrs)
        if iterations is not None and (
            inst := infra.opentelemetry.service.get_instrument("refiner_iters")
        ) is not None:
            inst.record(iterations, attributes = attrs)
    except Exception:
        pass


def record_bucket_split_overflow(*, framework: str, sections_dropped: int) -> None:
    """Increment when Phase A.5 hits the section cap."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("bucket_split_overflow")) is not None:
            inst.add(
                1,
                attributes = {
                    "framework":        framework,
                    "sections_dropped": str(sections_dropped),
                },
            )
    except Exception:
        pass


def record_grader_dim_score(*, framework: str, dim: str, score: float) -> None:
    """One grader dim's score (0-1). Dual-write: OTel histogram → Mimir
    for aggregates; LangFuse score → per-trace inspection in the UI."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("grader_dim_score")) is not None:
            inst.record(score, attributes = {"framework": framework, "dim": dim})
    except Exception:
        pass
    try:
        infra.langfuse.service.record_score(
            f"grader.{dim}",
            score,
            comment = f"framework={framework}",
        )
    except Exception:
        pass


def record_audit_missing(
    *,
    framework: str,
    chapter_number: int,
    iteration: int,
    missing_ratio: float,
) -> None:
    """Audit's missing-hash ratio for one iter."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("audit_missing_ratio")) is not None:
            inst.record(
                missing_ratio,
                attributes = {
                    "framework":      framework,
                    "chapter_number": str(chapter_number),
                    "iteration":      str(iteration),
                },
            )
    except Exception:
        pass


def record_study_completion(
    *,
    framework: str,
    duration_s: float,
    n_accepted: int,
    n_total: int,
    outcome: str,
) -> None:
    """End-to-end study completion (called from the assembler node)."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("study_completion_duration")) is not None:
            inst.record(
                duration_s,
                attributes = {
                    "framework":      framework,
                    "outcome":        outcome,
                    "accepted_count": str(n_accepted),
                    "total_chapters": str(n_total),
                },
            )
    except Exception:
        pass


def record_classical_patch(*, dim: str, framework: str) -> None:
    """Increment when Phase 4 classical refiner applies a patch."""
    try:
        if (inst := infra.opentelemetry.service.get_instrument("classical_patch_applied")) is not None:
            inst.add(1, attributes = {"dim": dim, "framework": framework})
    except Exception:
        pass
