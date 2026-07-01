"""outline_sdp — SurveyGen-I (arXiv 2508.14317) structure-driven chapter outliner."""
from .node import outline_sdp
from .schemas import (
    ChapterOutline,
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
