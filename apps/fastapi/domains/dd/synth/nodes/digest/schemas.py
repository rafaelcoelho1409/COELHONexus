"""digest_construct — Pydantic schemas (LLM output + persisted blob)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .params import (
    KEY_FACT_MAX_CHARS,
    KEY_FACT_MIN_CHARS,
    MAX_CONTRIBS_PER_SOURCE,
    MAX_KEY_FACTS_PER_CONTRIB,
    MIN_KEY_FACTS_PER_CONTRIB,
    OVERALL_SUMMARY_MAX_CHARS,
    OVERALL_SUMMARY_MIN_CHARS,
    SOURCE_TITLE_MAX_CHARS,
    SOURCE_TITLE_MIN_CHARS,
    SUMMARY_MAX_CHARS,
    SUMMARY_MIN_CHARS,
)
from .patterns import HASH_RE, SECTION_ID_RE
from .versions import DIGEST_PROMPT_VERSION, DIGEST_SCHEMA_VERSION


Relevance = Literal["primary", "supporting", "tangential"]


class SectionContribution(BaseModel):
    """One source's contribution to ONE outline section."""
    section_id: str = Field(
        description = (
            "Outline section id this contribution targets. MUST be one "
            "of the section_ids listed in the prompt outline (s1..sN)."
        ),
    )
    relevance: Relevance = Field(
        description = (
            "How central this source is to the section: 'primary' = "
            "source is a main authority; 'supporting' = useful detail "
            "but not the main reference; 'tangential' = mentions in "
            "passing."
        ),
    )
    summary: str = Field(
        description = (
            "1-3 sentences (20-600 chars) summarizing exactly what THIS "
            "source contributes to THIS section."
        ),
    )
    key_facts: list[str] = Field(
        description = (
            "1-5 concrete extractable claims (6-300 chars each), one per "
            "line. Each fact should be standalone."
        ),
    )
    code_refs: list[str] = Field(
        default_factory = list,
        description = (
            "Vault hashes from THIS source that belong to THIS section. "
            "Each entry must be a 16-hex string."
        ),
    )

    @field_validator("section_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not SECTION_ID_RE.match(v):
            raise ValueError(
                f"section_id {v!r} must match /^s\\d+$/ (e.g. 's1')"
            )
        return v

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (SUMMARY_MIN_CHARS <= len(s) <= SUMMARY_MAX_CHARS):
            raise ValueError(
                f"summary must be {SUMMARY_MIN_CHARS}-{SUMMARY_MAX_CHARS} "
                f"chars; got {len(s)}"
            )
        return s

    @field_validator("key_facts")
    @classmethod
    def _validate_facts(cls, v: list[str]) -> list[str]:
        if not (
            MIN_KEY_FACTS_PER_CONTRIB <= len(v) <= MAX_KEY_FACTS_PER_CONTRIB
        ):
            raise ValueError(
                f"key_facts count must be {MIN_KEY_FACTS_PER_CONTRIB}-"
                f"{MAX_KEY_FACTS_PER_CONTRIB}; got {len(v)}"
            )
        cleaned: list[str] = []
        for f in v:
            s = " ".join(f.strip().split())
            if not (KEY_FACT_MIN_CHARS <= len(s) <= KEY_FACT_MAX_CHARS):
                raise ValueError(
                    f"key_fact length must be {KEY_FACT_MIN_CHARS}-"
                    f"{KEY_FACT_MAX_CHARS} chars; got {len(s)} for {f!r}"
                )
            cleaned.append(s)
        return cleaned

    @field_validator("code_refs")
    @classmethod
    def _validate_refs(cls, v: list[str]) -> list[str]:
        for h in v:
            if not HASH_RE.match(h):
                raise ValueError(
                    f"code_ref {h!r} must be 16 lowercase hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(
                f"duplicate code_refs in same contribution: {v}"
            )
        return v


class LLMDigestPayload(BaseModel):
    """What the LLM returns. The source_key is injected by the node code
    (we know it; LLM doesn't need to echo it). Other fields are pure LLM
    output."""
    source_title: str = Field(
        description = (
            "A concise title for this source page (3-200 chars)."
        ),
    )
    overall_summary: str = Field(
        description = (
            "1-paragraph overall summary (30-800 chars). NOT keyed to "
            "any section."
        ),
    )
    contributes_to: list[SectionContribution] = Field(
        description = (
            "List of contributions, one per outline section this source "
            "ACTUALLY contributes to. 0-20 entries."
        ),
    )
    unassigned_code_refs: list[str] = Field(
        default_factory = list,
        description = (
            "Vault hashes present in this source that you couldn't "
            "confidently route to a specific section."
        ),
    )

    @field_validator("source_title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (
            SOURCE_TITLE_MIN_CHARS <= len(s) <= SOURCE_TITLE_MAX_CHARS
        ):
            raise ValueError(
                f"source_title length must be {SOURCE_TITLE_MIN_CHARS}-"
                f"{SOURCE_TITLE_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("overall_summary")
    @classmethod
    def _validate_overall(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (
            OVERALL_SUMMARY_MIN_CHARS <= len(s) <= OVERALL_SUMMARY_MAX_CHARS
        ):
            raise ValueError(
                f"overall_summary length must be "
                f"{OVERALL_SUMMARY_MIN_CHARS}-{OVERALL_SUMMARY_MAX_CHARS} "
                f"chars; got {len(s)}"
            )
        return s

    @field_validator("contributes_to")
    @classmethod
    def _validate_contribs(
        cls, v: list[SectionContribution],
    ) -> list[SectionContribution]:
        if len(v) > MAX_CONTRIBS_PER_SOURCE:
            raise ValueError(
                f"contributes_to has {len(v)} entries; max "
                f"{MAX_CONTRIBS_PER_SOURCE}"
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
            if not HASH_RE.match(h):
                raise ValueError(
                    f"unassigned code_ref {h!r} must be 16 hex chars"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate unassigned_code_refs: {v}")
        return v


class SourceDigest(BaseModel):
    """A single source's digest. Persisted in the chapter digest blob."""
    source_key: str
    source_title: str
    overall_summary: str
    contributes_to: list[SectionContribution]
    unassigned_code_refs: list[str] = Field(default_factory = list)
    deployment: Optional[str] = None
    wall_ms: Optional[int] = None


class CoverageStats(BaseModel):
    """Aggregate coverage metrics over the chapter digest."""
    n_sources:                int
    n_sections:               int
    sections_with_primary:    int
    empty_sections:           list[str]
    over_spread_sources:      list[str]
    orphan_code_refs:         int
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
    # source-pool merge result.
    merged_sections: dict[str, str] = Field(default_factory = dict)
