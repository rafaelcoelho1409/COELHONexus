from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .constants import (
    _CHALLENGES_MAX,
    _CHALLENGES_MIN,
    _DESCRIPTION_MAX_CHARS,
    _DESCRIPTION_MIN_CHARS,
    _FLASHCARDS_MAX,
    _FLASHCARDS_MIN,
    _HEADING_MAX_WORDS,
    _HEADING_MIN_WORDS,
    _MAX_PREREQS_PER_NODE,
    _SECTION_ID_RE,
    _SECTIONS_MAX,
    _SECTIONS_MIN,
)


# =============================================================================
# Pydantic schemas
# =============================================================================
class Flashcard(BaseModel):
    """Anki-style stand-alone Q/A pair."""
    q: str = Field(min_length=4, max_length=500,
                   description="Question. Concrete, code-focused where possible.")
    a: str = Field(min_length=2, max_length=1500,
                   description="Answer. 1-3 short paragraphs or a snippet.")


class OutlineSection(BaseModel):
    """
    One pre-allocated section: scaffold only, no body or code yet.
    `digest_construct` (next graph node) routes per-source content to
    sections by reasoning over `heading + description`. `sawc_write`
    then synthesizes prose + code per section, respecting `prerequisites`
    via stage-parallel execution.
    """
    section_id: str = Field(
        description=(
            "Stable lowercase identifier 's1', 's2', ... 's999'. MUST be "
            "unique within the chapter. Subsequent graph nodes (digest, "
            "sawc, mgsr_replan) reference sections by this id; once "
            "assigned, the id is permanent for the lifetime of the "
            "chapter outline."
        ),
    )
    heading: str = Field(
        description=(
            "Section heading WITHOUT leading '#'. 2-8 words, concrete, "
            "code-y or topic-y. Examples: 'Async Client', 'Dependency "
            "Injection', 'Tool Calling'. Avoid 'Introduction', "
            "'Overview', 'Summary', 'Conclusion', 'Getting Started'."
        ),
    )
    description: str = Field(
        description=(
            "1-line topic description (20-400 chars). Specific enough "
            "for digest_construct to route source material accurately. "
            "Examples: 'how to wire DI overrides for tests', 'the "
            "streaming response shape for tool calls'. Avoid vague "
            "descriptions like 'covers various features'."
        ),
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description=(
            "Section_ids of OTHER sections in this chapter that the "
            "reader must absorb BEFORE this one. List 0-3 ids. The first "
            "logical section (lowest stage) MUST have an empty list; "
            "later sections may name 0-3 prereqs that are STRUCTURALLY "
            "(not just thematically) required. If section B's code "
            "examples require concepts from A, list A in B.prerequisites."
        ),
    )
    needs_code: bool = Field(
        default=True,
        description=(
            "True if this section discusses code patterns / APIs / "
            "configs (so the assigned sources will contain vault code "
            "sentinels). False for design narratives, ecosystem "
            "discussion, or pure conceptual material. digest_construct "
            "uses this to weight code-heavy vs prose-heavy sources."
        ),
    )

    @field_validator("section_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _SECTION_ID_RE.match(v):
            raise ValueError(
                f"section_id {v!r} must match /^s\\d+$/ (e.g. 's1', 's12')"
            )
        return v

    @field_validator("heading")
    @classmethod
    def _validate_heading(cls, v: str) -> str:
        words = v.strip().split()
        if not (_HEADING_MIN_WORDS <= len(words) <= _HEADING_MAX_WORDS):
            raise ValueError(
                f"heading must be {_HEADING_MIN_WORDS}-{_HEADING_MAX_WORDS} "
                f"words; got {len(words)} ({v!r})"
            )
        if v.lstrip().startswith("#"):
            raise ValueError("heading must NOT start with '#'")
        return v.strip()

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_DESCRIPTION_MIN_CHARS <= len(s) <= _DESCRIPTION_MAX_CHARS):
            raise ValueError(
                f"description must be "
                f"{_DESCRIPTION_MIN_CHARS}-{_DESCRIPTION_MAX_CHARS} chars; "
                f"got {len(s)}"
            )
        return s

    @field_validator("prerequisites")
    @classmethod
    def _validate_prereqs(cls, v: list[str]) -> list[str]:
        if len(v) > _MAX_PREREQS_PER_NODE:
            raise ValueError(
                f"max {_MAX_PREREQS_PER_NODE} prerequisites per section; "
                f"got {len(v)}"
            )
        for prereq in v:
            if not _SECTION_ID_RE.match(prereq):
                raise ValueError(
                    f"prerequisite {prereq!r} must match section_id format"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate prerequisites: {v}")
        return v


class ChapterOutline(BaseModel):
    """
    Phase output of `outline_sdp` for one chapter. Carries:
      - sections: 4-40 scaffold entries
      - challenges: 5-10 active-recall questions (chapter-level)
      - flashcards: 4-15 Q/A pairs (chapter-level)

    The DAG (edges + stage_index + stages) is NOT a Pydantic field — it's
    derived post-LLM by `build_dag` + `compute_stage_indices` so the
    arithmetic is separated from LLM judgment. See `OutlineDAG` for the
    derived bundle.
    """
    sections: list[OutlineSection] = Field(
        min_length=_SECTIONS_MIN,
        max_length=_SECTIONS_MAX,
    )
    challenges: list[str] = Field(
        min_length=_CHALLENGES_MIN,
        max_length=_CHALLENGES_MAX,
        description=(
            "5-10 active-recall questions. Mix conceptual ('Why does X "
            "block on Y?') and applied ('Write a function that uses Z'). "
            "Each item is a single question string."
        ),
    )
    flashcards: list[Flashcard] = Field(
        min_length=_FLASHCARDS_MIN,
        max_length=_FLASHCARDS_MAX,
    )


class OutlineDAG(BaseModel):
    """Post-LLM derivation: edges + stage assignment + cycle audit.

    Computed by `derive_dag` from a validated `ChapterOutline`. Bundled
    alongside the outline in MinIO so downstream nodes don't re-compute.
    """
    edges: list[tuple[str, str]]
    stage_index: dict[str, int]
    stages: dict[int, list[str]]
    max_stage: int
    removed_edges: list[tuple[str, str]] = Field(default_factory=list)
