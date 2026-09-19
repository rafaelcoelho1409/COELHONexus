"""Pure helpers for the resume catch-up path (detect nodes missing from threads that already reached END)."""
from __future__ import annotations
import domains



def missing_implemented_nodes(state: dict) -> list[str]:
    """IMPLEMENTED node names whose primary output field is missing/empty."""
    missing: list[str] = []
    for name in domains.dd.planner.graph.IMPLEMENTED:
        field = domains.dd.planner.graph.NODE_TO_FIELD.get(name)
        if not field:
            continue
        val = state.get(field)
        if val is None or val == "" or val == []:
            missing.append(name)
    return missing


def derive_langfuse_output(
    state: dict,
    *,
    fallback_slug: str,
    fallback_mode: str,
    status: str,
    error: str | None,
) -> dict:
    """Pure state → Langfuse trace-output summary, given an already-fetched
    LangGraph state snapshot."""
    output: dict = {
        "status": status,
        "framework_slug": str(state.get("framework_slug") or fallback_slug or "unknown"),
        "mode": str(state.get("planner_mode") or fallback_mode or "unknown"),
    }
    if error:
        output["error"] = error
    select_stats = state.get("select_stats") or {}
    plan_write_stats = state.get("plan_write_stats") or {}
    order_stats = state.get("order_chapters_stats") or {}
    chapter_titles = (
        plan_write_stats.get("chapter_titles")
        or select_stats.get("chapter_titles")
        or order_stats.get("chapter_titles")
        or []
    )
    output["chapter_count"] = (
        plan_write_stats.get("n_chapters")
        or select_stats.get("n_chapters_out")
        or order_stats.get("n_chapters")
        or 0
    )
    if chapter_titles:
        output["chapter_titles"] = list(chapter_titles)[:12]
    return output
