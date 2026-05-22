"""sawc_write — Structure-Aware Writing Controller library.

Pure module: Pydantic schemas + prompt templates + deterministic
validators + memory-ledger helpers + structural draft scoring.
No I/O, no LLM calls — that lives in `synth/sawc/node.py`.

ARCHITECTURE — combines three published patterns

  1. SurveyGen-I PlanEvo SAWC (arXiv 2508.14317 §3.2)
     - Stage-parallel scheduling: `for t = 0..max τ; for all s_i ∈ S_t`
       sequential across stages, parallel within stage
     - Memory ledger M: accumulates prior section content + extracted
       terminology; later-stage sections see compressed memory of
       earlier-stage sections so cross-section coherence holds
     - Ablation evidence: w/o plan-update drops STRUC by 0.42 / CONSIS
       by 0.28 -> memory is what keeps structural cohesion

  2. MAMM-Refine multi-agent recipe (arXiv 2503.15272 §4)
     - N=3 writer drafts from distinct rotator picks (writer pool)
     - 1 critic-picker call from a DIFFERENT model family (critic pool)
       -> less-correlated errors than dual-same-family
     - Picker as RERANKER, not regenerator (paper finding: rerank > regen)
     - Constraint: similar-capability agents; large ability gaps reduce
       gains. Our writer/critic pools are all "free-tier frontier" so
       this holds.

  3. Self-Certainty (arXiv 2502.18581, Feb 2025)
     - Reward-free best-of-N selection via output probability distribution
     - We use a STRUCTURAL scoring proxy (no logprobs available from the
       bandit rotator) as the picker-fallback when the critic LLM fails
       -- same shape: pick by scalar quality estimate
"""
from .constants import (
    SAWC_SCHEMA_VERSION,
    SAWC_PROMPT_VERSION,
    _N_DRAFTS,
    _MAX_REPAIR_ATTEMPTS,
    _PARAGRAPHS_MIN,
    _PARAGRAPHS_MAX,
    _PARAGRAPH_CHARS_MIN,
    _PARAGRAPH_CHARS_MAX,
    _HEADING_MIN_WORDS,
    _HEADING_MAX_WORDS,
    _CODE_REFS_MAX,
    _CITATIONS_MIN,
    _CITATIONS_MAX,
    _CITATION_CLAIM_CHARS_MIN,
    _CITATION_CLAIM_CHARS_MAX,
    _PLACEMENT_HINT_CHARS_MIN,
    _PLACEMENT_HINT_CHARS_MAX,
    _MEMORY_TERMS_MIN,
    _MEMORY_TERMS_MAX,
    _MEMORY_TERM_CHARS_MIN,
    _MEMORY_TERM_CHARS_MAX,
    _MEMORY_SUMMARY_CHARS_MIN,
    _MEMORY_SUMMARY_CHARS_MAX,
    _HASH_RE,
    _SECTION_ID_RE,
)
from .types import (
    Citation,
    CodeRef,
    _LLMSectionDraft,
    Section,
    MemoryEntry,
    SAWCStats,
    ChapterDraft,
)
from .service import (
    extract_memory_entry,
    validate_section_against_inputs,
    score_draft_structural,
    compute_sawc_stats,
    _format_contributions_block,
    _format_memory_block,
    build_writer_prompt,
    build_critic_picker_prompt,
    build_repair_prompt,
    summarize_candidate,
)

__all__ = [
    "SAWC_SCHEMA_VERSION",
    "SAWC_PROMPT_VERSION",
    "_N_DRAFTS",
    "_MAX_REPAIR_ATTEMPTS",
    "_PARAGRAPHS_MIN",
    "_PARAGRAPHS_MAX",
    "_PARAGRAPH_CHARS_MIN",
    "_PARAGRAPH_CHARS_MAX",
    "_HEADING_MIN_WORDS",
    "_HEADING_MAX_WORDS",
    "_CODE_REFS_MAX",
    "_CITATIONS_MIN",
    "_CITATIONS_MAX",
    "_CITATION_CLAIM_CHARS_MIN",
    "_CITATION_CLAIM_CHARS_MAX",
    "_PLACEMENT_HINT_CHARS_MIN",
    "_PLACEMENT_HINT_CHARS_MAX",
    "_MEMORY_TERMS_MIN",
    "_MEMORY_TERMS_MAX",
    "_MEMORY_TERM_CHARS_MIN",
    "_MEMORY_TERM_CHARS_MAX",
    "_MEMORY_SUMMARY_CHARS_MIN",
    "_MEMORY_SUMMARY_CHARS_MAX",
    "_HASH_RE",
    "_SECTION_ID_RE",
    "Citation",
    "CodeRef",
    "_LLMSectionDraft",
    "Section",
    "MemoryEntry",
    "SAWCStats",
    "ChapterDraft",
    "extract_memory_entry",
    "validate_section_against_inputs",
    "score_draft_structural",
    "compute_sawc_stats",
    "_format_contributions_block",
    "_format_memory_block",
    "build_writer_prompt",
    "build_critic_picker_prompt",
    "build_repair_prompt",
    "summarize_candidate",
]
