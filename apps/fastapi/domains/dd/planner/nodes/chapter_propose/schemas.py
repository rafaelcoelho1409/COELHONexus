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
        # Repair, don't reject: clip to CONCEPTS_MAX, drop out-of-range /
        # duplicate concepts; only a real shortfall raises.
        out: list[str] = []
        seen: set[str] = set()
        for c in v:
            s = " ".join(c.strip().split())
            if not (params.CONCEPT_CHARS_MIN <= len(s) <= params.CONCEPT_CHARS_MAX):
                continue
            k = s.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(s)
            if len(out) >= params.CONCEPTS_MAX:
                break
        if len(out) < params.CONCEPTS_MIN:
            raise ValueError(
                f"only {len(out)} usable key_concepts "
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

    @field_validator("proposals", mode = "before")
    @classmethod
    def _drop_bad_proposals(cls, v):
        """Keep the valid proposals of a partly-bad sample (invalid items and
        case-insensitive duplicate titles are dropped, overflow clipped to
        PROPOSALS_MAX) instead of rejecting the whole sample."""
        if not isinstance(v, list):
            return v
        out: list = []
        seen: set[str] = set()
        for item in v:
            try:
                p = item if isinstance(item, ChapterProposal) else ChapterProposal.model_validate(item)
            except Exception:
                continue
            k = p.title.casefold()
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
            if len(out) >= params.PROPOSALS_MAX:
                break
        return out

    @field_validator("proposals")
    @classmethod
    def _validate_count(
        cls, v: list[ChapterProposal],
    ) -> list[ChapterProposal]:
        if len(v) < params.PROPOSALS_MIN:
            raise ValueError(
                f"only {len(v)} valid proposals "
                f"(minimum {params.PROPOSALS_MIN})"
            )
        return v


class VotePick(BaseModel):
    """USC-vote LLM output — which candidate sample to keep. Both fields
    required (not optional) — OpenAI strict json_schema mode requires
    every property in `required`, and the prompt already always asks
    for both."""
    model_config = ConfigDict(extra = "forbid")

    chosen_index: int = Field(description = "Index of the best candidate sample.")
    reason: str = Field(description = "Short justification.")
