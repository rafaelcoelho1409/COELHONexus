"""PlannerState TypedDict with one output field per node; all Optional so partial replays don't crash."""
from __future__ import annotations

from typing import Optional, TypedDict


class PlannerState(TypedDict, total=False):
    framework_slug: str
    thread_id: str               # also LangFuse session_id
    planner_mode: str            # future modes ("llm-fast"/"llm-thorough"); default "llm"

    raw_files: Optional[list[str]]              # corpus_load — MinIO keys only
    corpus_stats: Optional[dict]                # corpus_load — count/bytes/perc dist
    relevant_files: Optional[list[str]]         # off_topic — LLM-only filter (was post-embedding)
    off_topic_stats: Optional[dict]             # off_topic observability dict

    doc_distill_ref: Optional[str]              # doc_distill — MinIO key of {key→DocDistillate} JSON
    doc_distill_stats: Optional[dict]           # doc_distill — counts + skip-pass flag
    chapter_proposals_ref: Optional[str]        # chapter_propose — MinIO key of proposals JSON
    propose_stats: Optional[dict]               # chapter_propose — chosen titles for UI
    chapter_doc_assignments_ref: Optional[str]  # chapter_assign — MinIO key of doc×chapter matrix
    assign_stats: Optional[dict]                # chapter_assign — coverage counts
    # Field name predates the LLM-first rename (was the reduce-node output);
    # kept for backward compat with on-disk plan blobs.
    chapter_plan_ref: Optional[str]             # chapter_select — MinIO key of the outline JSON
    select_stats: Optional[dict]                # chapter_select — chapter sizes for UI

    # Pedagogical ordering; plan_write reads to reorder outline pre-sanitize.
    chapter_order_ref: Optional[str]            # order_chapters — MinIO key
    order_chapters_stats: Optional[dict]        # order_chapters — order + telemetry
    # Latest-pointer key (mutable plan-latest.json + versioned plan/{hash}.json).
    plan_path: Optional[str]                    # plan_write — MinIO key of latest pointer
    plan_write_stats: Optional[dict]            # plan_write — counts + inline plan for UI

    status: Optional[str]                       # "running" | "done" | "failed" | "cancelled"
    error: Optional[str]                        # last-node error, if any
