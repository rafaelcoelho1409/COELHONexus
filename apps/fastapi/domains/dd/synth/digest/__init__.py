"""digest_construct — LLM-assigned source-to-section routing library.

Pure module: Pydantic schemas + prompt templates + deterministic
aggregation. No I/O, no LLM calls — that lives in
`synth/digest/node.py`.

ARCHITECTURE — novel adaptation, May 2026 SOTA

Borrows two patterns from current literature and combines them:

  - PER-SOURCE LLM CALL (LLMxMapReduce-V3 arXiv 2510.10890 §3.2):
    "for each reference document, the system prompts an LLM to
    generate a brief summary along with suggestions for improving the
    current outline." We use the per-source LLM-call shape but apply
    it to a DIFFERENT problem (source-to-section routing), not the
    skeleton-refine problem the paper targets.

  - STRUCTURED PAPER CARD (IterSurvey arXiv 2510.21900):
    "Paper cards distill each paper into its contributions, methods,
    and findings." Same per-paper distillation pattern, tighter
    schema. Our `SourceDigest` is the technical-docs analog: one
    digest per source page, distilled into structured per-section
    contributions.

WHY THIS REPLACES THE DEPRECATED PHASE B (cosine routing)

Deprecated Phase B:
  - Embed (heading + goal) per section
  - Embed (prev_heading + code_signature) per vault hash
  - argmax(cosine) → assign hash to one section
  - Failure mode: ch02 content routed to ch04 (semantic neighbors
    confused by 120-char hash signatures)

New digest_construct:
  - Per source: 1 LLM call sees the FULL source + full outline
  - LLM reasons WHICH section(s) the source contributes to AND WHAT
    specifically, with typed relevance levels (primary/supporting/
    tangential)
  - LLM also routes vault sentinels to sections (replaces argmax)
  - Aggregate: deterministic invert → per-section table + coverage
    stats. NO second LLM consolidation pass (the per-source digests
    are already structured; deterministic merge is sufficient)

INPUT / OUTPUT

  Input (per source page):
    outline_compact          — list[{section_id, heading, description}]
    source_key               — MinIO key
    source_md                — full normalized + sentinelized markdown
    source_vault_hashes      — list[str] of 12-hex hashes present
                                (so the LLM only routes hashes it
                                ACTUALLY sees, not hallucinated ones)

  Output per source (Pydantic-validated):
    SourceDigest{
      source_key, source_title, overall_summary,
      contributes_to: [SectionContribution],
      unassigned_code_refs: [str]
    }

  Output for whole chapter (post-aggregate):
    ChapterDigest{
      chapter_id, framework_slug,
      per_source: list[SourceDigest],
      per_section: dict[section_id, list[SectionContribution]],
      coverage_stats: CoverageStats
    }

DOWNSTREAM CONSUMERS

  - `sawc_write` reads `per_section` to know which sources contribute
    what to each section. Section drafting is grounded on these
    digests (not on full source markdown).
  - `checklist_eval` reads `coverage_stats` to enforce per-section
    minimums (e.g. ≥1 primary source per section).
  - `mgsr_replan` reads coverage flags to emit replan actions when
    sections are empty or sources over-spread.

TUNABLES

  _CONTRIB_RELEVANCE_LEVELS    — Literal["primary","supporting","tangential"]
  _MAX_KEY_FACTS_PER_CONTRIB   = 5  (LLM-side hard cap; >5 = vague)
  _MIN_KEY_FACTS_PER_CONTRIB   = 1
  _MAX_CONTRIBS_PER_SOURCE     = 20 (one source rarely useful to >20)
  _OVER_SPREAD_THRESHOLD       = 3  (claim primary in >3 sections = sus)
"""
from .constants import (
    DIGEST_PROMPT_VERSION,
    DIGEST_SCHEMA_VERSION,
    _HASH_RE,
    _KEY_FACT_MAX_CHARS,
    _KEY_FACT_MIN_CHARS,
    _MAX_CONTRIBS_PER_SOURCE,
    _MAX_KEY_FACTS_PER_CONTRIB,
    _MIN_KEY_FACTS_PER_CONTRIB,
    _OVERALL_SUMMARY_MAX_CHARS,
    _OVERALL_SUMMARY_MIN_CHARS,
    _OVER_SPREAD_THRESHOLD,
    _SECTION_ID_RE,
    _SOURCE_TITLE_MAX_CHARS,
    _SOURCE_TITLE_MIN_CHARS,
    _SUMMARY_MAX_CHARS,
    _SUMMARY_MIN_CHARS,
    _VAULT_HASH_IN_TEXT_RE,
)
from .types import (
    ChapterDigest,
    CoverageStats,
    Relevance,
    SectionContribution,
    SourceDigest,
    _LLMDigestPayload,
)
from .service import (
    build_digest_prompt,
    build_per_section_index,
    build_repair_prompt,
    compute_coverage_stats,
    derive_source_title_fallback,
    extract_vault_hashes,
    validate_source_digest,
    _format_outline_compact,
)

__all__ = [
    # constants
    "DIGEST_PROMPT_VERSION",
    "DIGEST_SCHEMA_VERSION",
    "_HASH_RE",
    "_KEY_FACT_MAX_CHARS",
    "_KEY_FACT_MIN_CHARS",
    "_MAX_CONTRIBS_PER_SOURCE",
    "_MAX_KEY_FACTS_PER_CONTRIB",
    "_MIN_KEY_FACTS_PER_CONTRIB",
    "_OVERALL_SUMMARY_MAX_CHARS",
    "_OVERALL_SUMMARY_MIN_CHARS",
    "_OVER_SPREAD_THRESHOLD",
    "_SECTION_ID_RE",
    "_SOURCE_TITLE_MAX_CHARS",
    "_SOURCE_TITLE_MIN_CHARS",
    "_SUMMARY_MAX_CHARS",
    "_SUMMARY_MIN_CHARS",
    "_VAULT_HASH_IN_TEXT_RE",
    # types
    "ChapterDigest",
    "CoverageStats",
    "Relevance",
    "SectionContribution",
    "SourceDigest",
    "_LLMDigestPayload",
    # service
    "build_digest_prompt",
    "build_per_section_index",
    "build_repair_prompt",
    "compute_coverage_stats",
    "derive_source_title_fallback",
    "extract_vault_hashes",
    "validate_source_digest",
    "_format_outline_compact",
]
