"""outline_sdp — Pydantic schemas (ChapterOutline + OutlineDAG)."""
from __future__ import annotations
from . import params, patterns

from pydantic import BaseModel, Field, field_validator



class OutlineSection(BaseModel):
    """One pre-allocated section: scaffold only, no body or code yet."""
    section_id: str = Field(
        description = (
            "Stable lowercase identifier 's1', 's2', ... 's999'. MUST be "
            "unique within the chapter."
        ),
    )
    heading: str = Field(
        description = (
            "Section heading WITHOUT leading '#'. 2-8 words, concrete."
        ),
    )
    description: str = Field(
        description = (
            "1-line topic description (20-400 chars). Specific enough "
            "for digest_construct to route source material accurately."
        ),
    )
    prerequisites: list[str] = Field(
        default_factory = list,
        description = (
            "Section_ids of OTHER sections in this chapter that the "
            "reader must absorb BEFORE this one. List 0-3 ids."
        ),
    )
    needs_code: bool = Field(
        default = True,
        description = (
            "True if this section discusses code patterns / APIs / "
            "configs."
        ),
    )

    @field_validator("section_id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not patterns.SECTION_ID_RE.match(v):
            raise ValueError(
                f"section_id {v!r} must match /^s\\d+$/ "
                f"(e.g. 's1', 's12')"
            )
        return v

    @field_validator("heading")
    @classmethod
    def _validate_heading(cls, v: str) -> str:
        words = v.strip().split()
        if not (params.HEADING_MIN_WORDS <= len(words) <= params.HEADING_MAX_WORDS):
            raise ValueError(
                f"heading must be {params.HEADING_MIN_WORDS}-{params.HEADING_MAX_WORDS} "
                f"words; got {len(words)} ({v!r})"
            )
        if v.lstrip().startswith("#"):
            raise ValueError("heading must NOT start with '#'")
        return v.strip()

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (params.DESCRIPTION_MIN_CHARS <= len(s) <= params.DESCRIPTION_MAX_CHARS):
            raise ValueError(
                f"description must be {params.DESCRIPTION_MIN_CHARS}-"
                f"{params.DESCRIPTION_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("prerequisites")
    @classmethod
    def _validate_prereqs(cls, v: list[str]) -> list[str]:
        if len(v) > params.MAX_PREREQS_PER_NODE:
            raise ValueError(
                f"max {params.MAX_PREREQS_PER_NODE} prerequisites per section; "
                f"got {len(v)}"
            )
        for prereq in v:
            if not patterns.SECTION_ID_RE.match(prereq):
                raise ValueError(
                    f"prerequisite {prereq!r} must match section_id format"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate prerequisites: {v}")
        return v


class ChapterOutline(BaseModel):
    """Phase output of `outline_sdp` for one chapter."""
    sections: list[OutlineSection] = Field(
        min_length = params.SECTIONS_MIN,
        max_length = params.SECTIONS_MAX,
    )


class OutlineDAG(BaseModel):
    """Post-LLM derivation: edges + stage assignment + cycle audit."""
    edges: list[tuple[str, str]]
    stage_index: dict[str, int]
    stages: dict[int, list[str]]
    max_stage: int
    removed_edges: list[tuple[str, str]] = Field(default_factory = list)


OUTLINE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "chapter_outline",
        "schema": ChapterOutline.model_json_schema(),
        "strict": False,
    },
}

USC_VOTE_RESPONSE_FORMAT = {"type": "json_object"}
