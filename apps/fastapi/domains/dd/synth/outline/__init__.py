"""outline_sdp subpackage — re-exports all public names."""

from .constants import (
    OUTLINE_PROMPT_VERSION,
    OUTLINE_SCHEMA_VERSION,
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
from .types import (
    ChapterOutline,
    Flashcard,
    OutlineDAG,
    OutlineSection,
)

__all__ = [
    "OUTLINE_PROMPT_VERSION",
    "OUTLINE_SCHEMA_VERSION",
    "ChapterOutline",
    "Flashcard",
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
    "summarize_candidate",
    "validate_outline_structure",
]
