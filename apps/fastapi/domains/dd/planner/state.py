"""PlannerState — TypedDict that flows through every node.

One field per node's output. After a run completes the state carries
every intermediate artifact, so `/debug/graph/{thread_id}/state` can
expose what each substep produced (or didn't).

Fields are deliberately Optional so the no-op skeleton can build an
empty state without LangGraph complaining about missing keys, and so a
partial replay (e.g. fork at `dedup` checkpoint) starts from whatever
the earlier nodes wrote.
"""
from __future__ import annotations

from typing import Optional, TypedDict


class PlannerState(TypedDict, total=False):
    # --- inputs (set at graph kick-off) ---
    framework_slug: str
    thread_id: str               # also LangFuse session_id
    # Reserved for future modes (e.g. "llm-fast" / "llm-thorough"). Today
    # only "llm" is wired; nodes branch on this field when alternative
    # modes ship. Default "llm".
    planner_mode: str

    # --- node outputs (one per substep) ---
    raw_files: Optional[list[str]]              # corpus_load — MinIO keys only
    corpus_stats: Optional[dict]                # corpus_load — count/bytes/perc dist
    # embed_corpus stores the actual {key→vector} blob in MinIO (LangGraph
    # checkpoint must stay small — 2k docs × 2k dims as a Python list would
    # bloat Postgres ~80 MB). State carries only the pointer + meta.
    embeddings_ref: Optional[str]               # embed_corpus — MinIO key of the .npz blob
    embed_stats: Optional[dict]                 # embed_corpus — files/dim/cache_hit/wall_ms
    relevant_files: Optional[list[str]]         # off_topic (post-embedding filter)
    off_topic_stats: Optional[dict]             # off_topic observability dict

    # --- LLM-first planner path (canonical since 2026-05-27) ---
    # Per docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md.
    doc_distill_ref: Optional[str]              # doc_distill — MinIO key of {key→DocDistillate} JSON
    doc_distill_stats: Optional[dict]           # doc_distill — counts + skip-pass flag
    chapter_proposals_ref: Optional[str]        # chapter_propose — MinIO key of proposals JSON
    propose_stats: Optional[dict]               # chapter_propose — chosen titles for UI
    chapter_doc_assignments_ref: Optional[str]  # chapter_assign — MinIO key of doc×chapter matrix
    assign_stats: Optional[dict]                # chapter_assign — coverage counts
    # chapter_select writes the consumer-facing outline blob here. The
    # field name predates the LLM-first rename (was the reduce-node
    # output) — kept for backward compat with on-disk plan blobs.
    chapter_plan_ref: Optional[str]             # chapter_select — MinIO key of the outline JSON
    select_stats: Optional[dict]                # chapter_select — chapter sizes for UI

    # --- shared tail ---
    # order_chapters output (Bundle 8, 2026-05-25) — pedagogical ordering.
    # Stored as MinIO JSON with {order, samples, foundational_idx, ...}.
    # plan_write reads this to reorder the outline before sanitization.
    chapter_order_ref: Optional[str]            # order_chapters — MinIO key
    order_chapters_stats: Optional[dict]        # order_chapters — order + telemetry
    # plan_write output — consumer-facing final plan with hydrated
    # `sources` per chapter, written as `planner/{slug}/plan-latest.json`
    # (mutable pointer) AND `planner/{slug}/plan/{hash}.json` (versioned).
    # State carries the latest-pointer key + summary stats (with the
    # inline plan for UI rendering).
    plan_path: Optional[str]                    # plan_write — MinIO key of latest pointer
    plan_write_stats: Optional[dict]            # plan_write — counts + inline plan for UI

    # --- bookkeeping ---
    status: Optional[str]                       # "running" | "done" | "failed" | "cancelled"
    error: Optional[str]                        # last-node error, if any
