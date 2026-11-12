"""PlannerState: one output field per node, all optional so a partial replay never crashes. Field names are read by name by the web."""
from __future__ import annotations

import typing


class PlannerState(typing.TypedDict, total = False):
    framework_slug: str
    thread_id: str               # also LangFuse session_id
    planner_mode: str            # future modes ("llm-fast"/"llm-thorough"); default "llm"
    planner_version: str         # "v1" or "v2"; recorded in the output plan

    raw_files: typing.Optional[list[str]]              # corpus_load — MinIO keys only
    corpus_stats: typing.Optional[dict]                # corpus_load — count/bytes/perc dist
    relevant_files: typing.Optional[list[str]]         # off_topic — LLM-only filter
    off_topic_stats: typing.Optional[dict]             # off_topic observability dict

    page_cards_ref: typing.Optional[str]               # page_cards — content-addressed semantic cards
    page_cards_stats: typing.Optional[dict]             # page_cards — keep/drop/cache/embed counts
    chapter_design_stats: typing.Optional[dict]         # chapter_design — grouping and coverage diagnostics

    doc_distill_ref: typing.Optional[str]              # doc_distill — MinIO key of {key→DocDistillate} JSON
    doc_distill_stats: typing.Optional[dict]           # doc_distill — counts + skip-pass flag
    chapter_proposals_ref: typing.Optional[str]        # chapter_propose — MinIO key of proposals JSON
    propose_stats: typing.Optional[dict]               # chapter_propose — chosen titles for UI
    chapter_doc_assignments_ref: typing.Optional[str]  # chapter_assign — MinIO key of doc×chapter matrix
    assign_stats: typing.Optional[dict]                # chapter_assign — coverage counts
    # Field name predates the LLM-first rename (was the reduce-node output); kept for backward compat with on-disk plan blobs.
    chapter_plan_ref: typing.Optional[str]             # chapter_select — MinIO key of the outline JSON
    select_stats: typing.Optional[dict]                # chapter_select — chapter sizes for UI

    # Pedagogical ordering; plan_write reads to reorder the outline before sanitizing.
    chapter_order_ref: typing.Optional[str]            # order_chapters — MinIO key
    order_chapters_stats: typing.Optional[dict]        # order_chapters — order + telemetry
    # Latest-pointer key (mutable plan-latest.json + versioned plan/{hash}.json).
    plan_path: typing.Optional[str]                    # plan_write — MinIO key of latest pointer
    plan_write_stats: typing.Optional[dict]            # plan_write — counts + inline plan for UI

    status: typing.Optional[str]                       # "running" | "done" | "needs_review" | "failed" | "cancelled"
    error: typing.Optional[str]                        # last-node error, if any
