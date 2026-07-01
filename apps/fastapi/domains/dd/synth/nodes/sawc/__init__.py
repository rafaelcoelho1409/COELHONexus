"""sawc_write — Stage-Aware Writer-Critic: SurveyGen-I SAWC (arXiv 2508.14317) + MAMM-Refine N-drafts (arXiv 2503.15272) + Self-Certainty fallback (arXiv 2502.18581)."""
from .node import sawc_write
from .schemas import (
    ChapterDraft,
    Citation,
    LLMSectionDraft,
    MemoryEntry,
    SAWCStats,
    Section,
    Subtopic,
)
from .service import (
    build_critic_picker_prompt,
    build_repair_prompt,
    build_writer_prompt,
    compute_sawc_stats,
    extract_memory_entry,
    score_draft_structural,
    summarize_candidate,
    validate_section_against_inputs,
)
from .versions import SAWC_PROMPT_VERSION, SAWC_SCHEMA_VERSION


__all__ = [
    "ChapterDraft",
    "Citation",
    "LLMSectionDraft",
    "MemoryEntry",
    "SAWC_PROMPT_VERSION",
    "SAWC_SCHEMA_VERSION",
    "SAWCStats",
    "Section",
    "Subtopic",
    "build_critic_picker_prompt",
    "build_repair_prompt",
    "build_writer_prompt",
    "compute_sawc_stats",
    "extract_memory_entry",
    "sawc_write",
    "score_draft_structural",
    "summarize_candidate",
    "validate_section_against_inputs",
]
