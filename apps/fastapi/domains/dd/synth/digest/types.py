"""digest_construct types — Pydantic schemas + type aliases."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

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
    _SECTION_ID_RE,
    _SOURCE_TITLE_MAX_CHARS,
    _SOURCE_TITLE_MIN_CHARS,
    _SUMMARY_MAX_CHARS,
    _SUMMARY_MIN_CHARS,
)

Relevance = Literal["primary", "supporting", "tangential"]


# =============================================================================
# Pydantic schemas — LLM output side
# =============================================================================
class SectionContribution(BaseModel):
    """One source's contribution to ONE outline section."""
    section_id: str = Field(
        description=(
            "Outline section id this contribution targets. MUST be one "
            "of the section_ids listed in the prompt outline (s1..sN)."
        ),
    )
    relevance: Relevance = Field(
        description=(
            "How central this source is to the section: "
            "'primary' = source is a main authority for the section's "
            "content; 'supporting' = useful detail but not the main "
            "reference; 'tangential' = mentions in passing."
        ),
    )
    summary: str = Field(
        description=(
            "1-3 sentences (20-600 chars) summarizing exactly what THIS "
            "source contributes to THIS section. Concrete, no vague "
            "phrases. Used by sawc_write as the grounded teaching "
            "material for the section."
        ),
    )
    key_facts: list[str] = Field(
        description=(
            "1-5 concrete extractable claims (6-300 chars each), one "
            "per line. Each fact should be standalone (no 'see above'). "
            "Examples: 'CountryAlpha2 inherits from str', 'Luhn check "
            "uses mod-10 weighted sum'. Used by sawc_write to ground "
            "claims with citations."
        ),
    )
    code_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Vault hashes from THIS source that belong to THIS section. "
            "Each entry must be a 16-hex string matching a hash listed "
            "in the prompt's `vault_hashes_in_source`. Empty list if "
            "the section's contribution is prose-only or no code refs "
            "from this source belong here."
        ),
    )

    @field_validator("section_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _SECTION_ID_RE.match(v):
            raise ValueError(
                f"section_id {v!r} must match /^s\\d+$/ (e.g. 's1')"
            )
        return v

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_SUMMARY_MIN_CHARS <= len(s) <= _SUMMARY_MAX_CHARS):
            raise ValueError(
                f"summary must be {_SUMMARY_MIN_CHARS}-"
                f"{_SUMMARY_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("key_facts")
    @classmethod
    def _validate_facts(cls, v: list[str]) -> list[str]:
        if not (_MIN_KEY_FACTS_PER_CONTRIB <= len(v)
                <= _MAX_KEY_FACTS_PER_CONTRIB):
            raise ValueError(
                f"key_facts count must be "
                f"{_MIN_KEY_FACTS_PER_CONTRIB}-"
                f"{_MAX_KEY_FACTS_PER_CONTRIB}; got {len(v)}"
            )
        cleaned: list[str] = []
        for f in v:
            s = " ".join(f.strip().split())
            if not (_KEY_FACT_MIN_CHARS <= len(s) <= _KEY_FACT_MAX_CHARS):
                raise ValueError(
                    f"key_fact length must be {_KEY_FACT_MIN_CHARS}-"
                    f"{_KEY_FACT_MAX_CHARS} chars; got {len(s)} "
                    f"for {f!r}"
                )
            cleaned.append(s)
        return cleaned

    @field_validator("code_refs")
    @classmethod
    def _validate_refs(cls, v: list[str]) -> list[str]:
        for h in v:
            if not _HASH_RE.match(h):
                raise ValueError(
                    f"code_ref {h!r} must be 16 lowercase hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate code_refs in same contribution: {v}")
        return v


class _LLMDigestPayload(BaseModel):
    """What the LLM returns. The source_key is injected by the node
    code (we know it; LLM doesn't need to echo it). Other fields are
    pure LLM output."""
    source_title: str = Field(
        description=(
            "A concise title for this source page (3-200 chars). "
            "Derive from the markdown's first H1 if present, or "
            "synthesize from the URL slug. Used by the UI when "
            "displaying digests."
        ),
    )
    overall_summary: str = Field(
        description=(
            "1-paragraph overall summary of what THIS source is about "
            "(30-800 chars). NOT keyed to any section — just the "
            "source's identity. Used by mgsr_replan and downstream "
            "audit to verify source identity."
        ),
    )
    contributes_to: list[SectionContribution] = Field(
        description=(
            "List of contributions, one per outline section this source "
            "ACTUALLY contributes to. Omit sections this source doesn't "
            "touch — don't pad with empty/'not applicable' entries. "
            "0-20 entries. A single source rarely contributes to >5 "
            "sections; many will only contribute to 1-3."
        ),
    )
    unassigned_code_refs: list[str] = Field(
        default_factory=list,
        description=(
            "Vault hashes present in this source that you couldn't "
            "confidently route to a specific section. Each entry must "
            "be a 16-hex hash from `vault_hashes_in_source`. Empty "
            "list if every present hash got routed."
        ),
    )

    @field_validator("source_title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_SOURCE_TITLE_MIN_CHARS <= len(s) <= _SOURCE_TITLE_MAX_CHARS):
            raise ValueError(
                f"source_title length must be {_SOURCE_TITLE_MIN_CHARS}-"
                f"{_SOURCE_TITLE_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("overall_summary")
    @classmethod
    def _validate_overall(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_OVERALL_SUMMARY_MIN_CHARS <= len(s)
                <= _OVERALL_SUMMARY_MAX_CHARS):
            raise ValueError(
                f"overall_summary length must be "
                f"{_OVERALL_SUMMARY_MIN_CHARS}-{_OVERALL_SUMMARY_MAX_CHARS} "
                f"chars; got {len(s)}"
            )
        return s

    @field_validator("contributes_to")
    @classmethod
    def _validate_contribs(
        cls, v: list[SectionContribution],
    ) -> list[SectionContribution]:
        if len(v) > _MAX_CONTRIBS_PER_SOURCE:
            raise ValueError(
                f"contributes_to has {len(v)} entries; max "
                f"{_MAX_CONTRIBS_PER_SOURCE} allowed (one source "
                f"is rarely useful in more sections than that)"
            )
        ids = [c.section_id for c in v]
        if len(set(ids)) != len(ids):
            raise ValueError(
                f"duplicate section_id in contributes_to: {ids}"
            )
        return v

    @field_validator("unassigned_code_refs")
    @classmethod
    def _validate_unassigned(cls, v: list[str]) -> list[str]:
        for h in v:
            if not _HASH_RE.match(h):
                raise ValueError(
                    f"unassigned code_ref {h!r} must be 16 hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate unassigned_code_refs: {v}")
        return v


# =============================================================================
# Pydantic schemas — persisted side (LLM output + node-injected fields)
# =============================================================================
class SourceDigest(BaseModel):
    """A single source's digest. Persisted in the chapter digest blob.

    Mirrors `_LLMDigestPayload` but adds node-injected `source_key`
    (we know it; the LLM doesn't need to echo it) and observability
    fields (`deployment`, `wall_ms`).
    """
    source_key: str
    source_title: str
    overall_summary: str
    contributes_to: list[SectionContribution]
    unassigned_code_refs: list[str] = Field(default_factory=list)
    deployment: Optional[str] = None
    wall_ms: Optional[int] = None


class CoverageStats(BaseModel):
    """Aggregate coverage metrics over the chapter digest.

    Drives:
      - `checklist_eval` per-section minimums
      - `mgsr_replan` replan actions (empty section -> merge/delete)
      - UI KPI badge (`src=N . cov=M/N . orph=K`)
    """
    n_sources:                int
    n_sections:               int
    sections_with_primary:    int   # count of sections with >=1 primary contributor
    empty_sections:           list[str]   # section_ids with 0 contributions
    over_spread_sources:      list[str]   # source_keys claiming primary in >threshold sections
    orphan_code_refs:         int          # vault hashes no section claimed
    avg_sources_per_section:  float
    avg_sections_per_source:  float


class ChapterDigest(BaseModel):
    """Full chapter digest — what gets persisted to MinIO."""
    schema_version: str = DIGEST_SCHEMA_VERSION
    prompt_version: str = DIGEST_PROMPT_VERSION
    chapter_id:     str
    chapter_title:  str
    framework_slug: str
    n_pydantic_fail: int = 0
    per_source: list[SourceDigest]
    per_section: dict[str, list[SectionContribution]]
    coverage_stats: CoverageStats
    # v3 (2026-05-29 PM) — source-pool merge result: {loser_section_id:
    # winner_section_id}. Losers have had their contributions folded into
    # the winner in `per_section` and carry an empty list; sawc_write skips
    # them so they never render. Empty when no sections were merged.
    merged_sections: dict[str, str] = Field(default_factory=dict)
