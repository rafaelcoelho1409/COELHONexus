"""outline_sdp — Structure-Driven Planner (SurveyGen-I arXiv 2508.14317).

Single LLM call per chapter produces a ChapterOutline with typed
prerequisites. The DAG (edges + stage indices) is derived deterministically
post-LLM. Same-stage sections are downstream-parallelizable by sawc_write.

See docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md.
"""
from .node import outline_sdp
from .schemas import (
    ChapterOutline,
    Flashcard,
    OutlineDAG,
    OutlineSection,
)
from .service import (
    break_cycles_fas,
    build_edges,
    build_outline_prompt,
    build_repair_prompt,
    build_usc_vote_prompt,
    compute_stage_indices,
    count_vault_sentinels,
    derive_dag,
    summarize_candidate,
    validate_outline_structure,
)
from .versions import OUTLINE_PROMPT_VERSION, OUTLINE_SCHEMA_VERSION


__all__ = [
    "ChapterOutline",
    "Flashcard",
    "OUTLINE_PROMPT_VERSION",
    "OUTLINE_SCHEMA_VERSION",
    "OutlineDAG",
    "OutlineSection",
    "break_cycles_fas",
    "build_edges",
    "build_outline_prompt",
    "build_repair_prompt",
    "build_usc_vote_prompt",
    "compute_stage_indices",
    "count_vault_sentinels",
    "derive_dag",
    "outline_sdp",
    "summarize_candidate",
    "validate_outline_structure",
]
