"""digest_construct — LLM-assigned source-to-section routing (LLMxMapReduce-V3 + IterSurvey schema)."""
from .domain import (
    build_per_section_index,
    compute_coverage_stats,
    derive_source_title_fallback,
    extract_vault_hashes,
    merge_overlapping_sections,
    validate_source_digest,
)
from .node import digest_construct
from .schemas import (
    ChapterDigest,
    CoverageStats,
    LLMDigestPayload,
    Relevance,
    SectionContribution,
    SourceDigest,
)
from .versions import DIGEST_PROMPT_VERSION, DIGEST_SCHEMA_VERSION


__all__ = [
    "ChapterDigest",
    "CoverageStats",
    "DIGEST_PROMPT_VERSION",
    "DIGEST_SCHEMA_VERSION",
    "LLMDigestPayload",
    "Relevance",
    "SectionContribution",
    "SourceDigest",
    "build_per_section_index",
    "compute_coverage_stats",
    "derive_source_title_fallback",
    "digest_construct",
    "extract_vault_hashes",
    "merge_overlapping_sections",
    "validate_source_digest",
]
