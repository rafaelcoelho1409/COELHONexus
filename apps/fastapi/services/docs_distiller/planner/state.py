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
    deduped_files: Optional[list[str]]          # dedup
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
    shard_results: Optional[list[dict]]         # map (per-shard labels + assignments)
    chapter_plan: Optional[list[dict]]          # reduce (final outline)
    validated_plan: Optional[list[dict]]        # validate (coverage-repaired plan)
    plan_path: Optional[str]                    # plan_write (MinIO key)

    # --- bookkeeping ---
    status: Optional[str]                       # "running" | "done" | "failed" | "cancelled"
    error: Optional[str]                        # last-node error, if any
