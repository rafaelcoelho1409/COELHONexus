"""chapter_propose — Pydantic value objects + LLM response_format specs."""
from __future__ import annotations
from . import params

from pydantic import BaseModel, ConfigDict, Field, field_validator



class ChapterProposal(BaseModel):
    """One candidate chapter from the proposer LLM."""
    # Groq strict json_schema mode requires additionalProperties:false on every
    # object, including nested $defs — extra="forbid" emits it here too.
    model_config = ConfigDict(extra = "forbid")

    title: str = Field(
        description = (
            f"{params.TITLE_MIN_WORDS}-{params.TITLE_MAX_WORDS} words. Concrete noun "
            f"phrase. Avoid generic 'Introduction', 'Overview', "
            f"'Conclusion' — name the specific topic."
        ),
    )
    description: str = Field(
        description = (
            f"{params.DESCRIPTION_CHARS_MIN}-{params.DESCRIPTION_CHARS_MAX} chars. One "
            f"sentence describing what readers learn in this chapter."
        ),
    )
    key_concepts: list[str] = Field(
        description = (
            f"{params.CONCEPTS_MIN}-{params.CONCEPTS_MAX} technical concepts/identifiers/"
            f"commands that belong in this chapter. Specific names, not "
            f"abstract topics."
        ),
    )

    @field_validator("title")
    @classmethod
    def _validate_title(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        n = len(s.split())
        if not (params.TITLE_MIN_WORDS <= n <= params.TITLE_MAX_WORDS):
            raise ValueError(
                f"title must be {params.TITLE_MIN_WORDS}-{params.TITLE_MAX_WORDS} "
                f"words; got {n}"
            )
        return s

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (params.DESCRIPTION_CHARS_MIN <= len(s) <= params.DESCRIPTION_CHARS_MAX):
            raise ValueError(
                f"description must be {params.DESCRIPTION_CHARS_MIN}-"
                f"{params.DESCRIPTION_CHARS_MAX} chars; got {len(s)}"
            )
        return s

    @field_validator("key_concepts")
    @classmethod
    def _validate_concepts(cls, v: list[str]) -> list[str]:
        if not (params.CONCEPTS_MIN <= len(v) <= params.CONCEPTS_MAX):
            raise ValueError(
                f"key_concepts count must be {params.CONCEPTS_MIN}-"
                f"{params.CONCEPTS_MAX}; got {len(v)}"
            )
        out: list[str] = []
        seen: set[str] = set()
        for c in v:
            s = " ".join(c.strip().split())
            if not (params.CONCEPT_CHARS_MIN <= len(s) <= params.CONCEPT_CHARS_MAX):
                raise ValueError(
                    f"concept length must be {params.CONCEPT_CHARS_MIN}-"
                    f"{params.CONCEPT_CHARS_MAX}; got {len(s)}"
                )
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
        if len(out) < params.CONCEPTS_MIN:
            raise ValueError(
                f"after dedup only {len(out)} key_concepts "
                f"(minimum {params.CONCEPTS_MIN})"
            )
        return out


class ChapterProposalList(BaseModel):
    """LLM output — a list of chapter proposals."""
    model_config = ConfigDict(extra = "forbid")

    proposals: list[ChapterProposal] = Field(
        description = (
            f"{params.PROPOSALS_MIN}-{params.PROPOSALS_MAX} chapter proposals covering "
            f"the full corpus surface area. Each chapter is a distinct "
            f"topic. Aim for balance — every chapter should be backed by "
            f"≥3 source docs."
        ),
    )

    @field_validator("proposals")
    @classmethod
    def _validate_count(
        cls, v: list[ChapterProposal],
    ) -> list[ChapterProposal]:
        if not (params.PROPOSALS_MIN <= len(v) <= params.PROPOSALS_MAX):
            raise ValueError(
                f"proposals count must be {params.PROPOSALS_MIN}-{params.PROPOSALS_MAX}; "
                f"got {len(v)}"
            )
        seen: set[str] = set()
        for p in v:
            k = p.title.casefold()
            if k in seen:
                raise ValueError(
                    f"duplicate chapter title (case-insensitive): "
                    f"{p.title!r}"
                )
            seen.add(k)
        return v


PROPOSE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "chapter_proposal_list",
        "schema": ChapterProposalList.model_json_schema(),
        "strict": True,
    },
}
VOTE_RESPONSE_FORMAT = {"type": "json_object"}
