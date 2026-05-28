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
    # "llm"        — every substep that has an LLM path uses the rotator
    # "classical"  — substeps with classical fallbacks (map → numpy
    #                community_detection + KeyLLM; etc.) use them instead.
    # Plumbed through state today; only the LLM path is implemented. When
    # classical lands, nodes branch on this field. Default "llm".
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
    # cluster output — soft-membership matrix (N×K) stored in MinIO; state
    # carries pointer + summary stats. refine + label + reduce nodes load
    # the .npz blob via load_clusters().
    cluster_assignments_ref: Optional[str]      # cluster — MinIO key of the .npz
    cluster_stats: Optional[dict]               # cluster — counts/sizes/wall
    # refine output — LITA boundary-doc reassignment. Stored as MinIO .npz
    # with (keys, refined_assignments, original_assignments, decisions_json).
    # State carries only the pointer + summary stats.
    refine_assignments_ref: Optional[str]       # refine — MinIO key of the .npz
    refine_stats: Optional[dict]                # refine — counts/changed/null/wall
    # label output — KeyLLM-style 2-4 word names per refined cluster.
    # Stored as MinIO JSON with {labels, n_round2, round1_decisions}.
    cluster_labels_ref: Optional[str]           # label — MinIO key of the JSON
    label_stats: Optional[dict]                 # label — counts + label map for UI
    # reduce output — 4-12 chapter outline merged from labeled clusters.
    # Stored as MinIO JSON with {outline, n_clusters_in, n_repairs, ...}.
    # State carries the pointer + summary stats (which include the full
    # outline inline since it's small — ~1-3 KB).
    chapter_plan_ref: Optional[str]             # reduce — MinIO key of the JSON
    reduce_stats: Optional[dict]                # reduce — counts + outline for UI
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

    # --- LLM-first planner state (2026-05-27, KD_PLANNER_LLM_FIRST=true) ---
    # Per docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md. These fields are
    # populated when the LLM-first path is active; legacy fields above
    # (cluster_assignments_ref, refine_assignments_ref, cluster_labels_ref)
    # stay None on the LLM-first path. plan_write tolerates either path.
    doc_distill_ref: Optional[str]              # doc_distill — MinIO key of {key→DocDistillate} JSON
    doc_distill_stats: Optional[dict]           # doc_distill — counts + skip-pass flag
    chapter_proposals_ref: Optional[str]        # chapter_propose — MinIO key of proposals JSON
    propose_stats: Optional[dict]               # chapter_propose — chosen titles for UI
    chapter_doc_assignments_ref: Optional[str]  # chapter_assign — MinIO key of doc×chapter matrix
    assign_stats: Optional[dict]                # chapter_assign — coverage counts
    select_stats: Optional[dict]                # chapter_select — chapter sizes for UI

    # --- bookkeeping ---
    status: Optional[str]                       # "running" | "done" | "failed" | "cancelled"
    error: Optional[str]                        # last-node error, if any
