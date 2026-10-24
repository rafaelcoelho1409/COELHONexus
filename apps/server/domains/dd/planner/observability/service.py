"""Planner observability — the `@traced` node decorator (one span per planner node, exported to Alloy + LangFuse) and the pipeline metric recorder."""
from __future__ import annotations
import domains, infra


def _llm_context(state: dict, node_name: str):
    """Attribute the LLM calls inside this node to (planner, thread, node)."""
    return domains.dd.llm_counter.service.llm_context("planner", state.get("thread_id") or "", node_name)


traced = infra.opentelemetry.service.node_tracer(
    prefix      = "planner",
    workflow    = "dd_planner",
    stage       = "planner",
    span_prefix = "dd.planner.node",
    state_attrs = lambda state: {
        "thread_id":      state.get("thread_id") or "",
        "framework_slug": state.get("framework_slug") or "",
    },
    context     = _llm_context,
)


def record_planner_run(
    *,
    framework: str,
    mode: str,
    outcome: str,
    duration_s: float | None = None,
    chapter_count: int | None = None,
) -> None:
    attrs = {
        "framework": framework,
        "mode":      mode or "unknown",
        "outcome":   outcome or "unknown",
    }
    try:
        if (inst := infra.opentelemetry.service.get_instrument("planner_run_total")) is not None:
            inst.add(1, attributes = attrs)
        if duration_s is not None and (
            inst := infra.opentelemetry.service.get_instrument("planner_run_duration")
        ) is not None:
            inst.record(max(duration_s, 0.0), attributes = attrs)
        if chapter_count is not None and (
            inst := infra.opentelemetry.service.get_instrument("planner_chapter_count")
        ) is not None:
            inst.record(max(chapter_count, 0), attributes = attrs)
    except Exception:
        pass
