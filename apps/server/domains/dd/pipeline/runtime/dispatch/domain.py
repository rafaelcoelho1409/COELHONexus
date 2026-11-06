"""Pure helpers of the pipeline's run machinery — thread-id parsing and the Langfuse trace-output summary of a planner run."""
from __future__ import annotations
import domains

import re
import uuid


def slug_from_thread_id(thread_id: str) -> str | None:
    """`docs-distiller/{slug}/{uuid}` → slug. None on a format mismatch."""
    parts = (thread_id or "").split("/", 2)
    if len(parts) >= 2 and parts[0] == domains.dd.pipeline.runtime.dispatch.keys.THREAD_PREFIX:
        return parts[1] or None
    return None


def make_thread_id(slug: str) -> str:
    """Per-planner-run thread_id. Format: `docs-distiller/{slug}/{uuid}`, the id the web pre-generates so Cancel works from click 1."""
    return f"{domains.dd.pipeline.runtime.dispatch.keys.THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


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


def make_chapter_thread_id(slug: str) -> str:
    """Per-chapter synth thread id; the web pre-generates ids in the same shape."""
    return f"{domains.dd.pipeline.runtime.dispatch.keys.CHAPTER_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


def make_study_thread_id(slug: str) -> str:
    """Per-study synth thread id, with a prefix distinct from a chapter's."""
    return f"{domains.dd.pipeline.runtime.dispatch.keys.STUDY_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


def slug_from_synth_thread_id(thread_id: str) -> str | None:
    """`docs-distiller/synth|study/{slug}/{uuid}` → slug. None on a format mismatch."""
    parts = (thread_id or "").split("/", 3)
    if len(parts) >= 3 and parts[0] == domains.dd.pipeline.runtime.dispatch.keys.THREAD_PREFIX and parts[1] in ("synth", "study"):
        return parts[2] or None
    return None


def chapter_number_from_id(chapter_id: str) -> int:
    """`ch-03-runtime` → 3; 0 when the id has no number."""
    match = re.match(r"^ch-(\d+)", chapter_id or "")
    return int(match.group(1)) if match else 0
