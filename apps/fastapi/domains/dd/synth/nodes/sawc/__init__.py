"""sawc_write — Structure-Aware Writing Controller (PILOT TARGET).

Combines three published patterns:
  1. SurveyGen-I PlanEvo SAWC (arXiv 2508.14317 §3.2) — stage-parallel
     scheduling + memory ledger M for cross-section coherence
  2. MAMM-Refine multi-agent recipe (arXiv 2503.15272 §4) — N=3 writer
     drafts + 1 cross-family critic-picker as reranker
  3. Self-Certainty (arXiv 2502.18581) — structural scoring fallback
     when the critic LLM fails

Per the conventions doc this leaf is the pattern-establishing module.
See docs/CODE-CONVENTIONS.md §5.
"""
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
