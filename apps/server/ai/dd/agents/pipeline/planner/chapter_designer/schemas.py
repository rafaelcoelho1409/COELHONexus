"""Structured answer for grouping the candidate page clusters."""
from __future__ import annotations

import typing

import pydantic


class DesignedChapter(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    title: str = pydantic.Field(description = "Concrete topic title, not a generic content type.")
    description: str = pydantic.Field(description = "One sentence describing what readers learn.")
    objective: str = pydantic.Field(description = "A measurable ability the reader has after this chapter.")
    group_ids: list[str] = pydantic.Field(
        description = "Globally unique short group_id aliases copied exactly from the selected partition; every alias appears once across all chapters.",
    )
    key_concepts: list[str] = pydantic.Field(default_factory = list)
    prerequisites: list[str] = pydantic.Field(default_factory = list, description = "Titles of chapters that should come first.")
    kind: typing.Literal["tutorial", "appendix"] = "tutorial"
    capstone_ability: bool = pydantic.Field(description = "Whether the chapter's best code examples can compose into one practical application.")
    capstone_reason: str = pydantic.Field(default = "", description = "Short reason for the capability decision.")
    synth_eligible: bool = pydantic.Field(
        default = True,
        description = "Whether this chapter should be synthesized into the main learning tutorial; lookup-only material may be excluded.",
    )
    synth_exclusion_reason: str = pydantic.Field(
        default = "",
        description = "Required when synth_eligible is false; explain why this material is unnecessary in the tutorial while remaining available as source reference.",
    )

    @pydantic.field_validator("group_ids", mode = "before")
    @classmethod
    def _clean_group_ids(cls, value):
        if not isinstance(value, list):
            return []
        # Preserve duplicates so assignment validation can report them instead of silently repairing them.
        out = [" ".join(str(item or "").split()) for item in value]
        return [item for item in out if item]

    @pydantic.field_validator("key_concepts", "prerequisites", mode = "before")
    @classmethod
    def _clean_lists(cls, value):
        if not isinstance(value, list):
            return []
        out = []
        seen = set()
        for item in value:
            text = " ".join(str(item or "").split())
            if text and text.casefold() not in seen:
                out.append(text)
                seen.add(text.casefold())
        return out


class ChapterDesignAnswer(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    candidate_partition_index: int = pydantic.Field(
        description = "Zero-based index of the selected candidate partition in the supplied candidate_partitions list; required for a valid design.",
    )
    chapters: list[DesignedChapter] = pydantic.Field(description = "The complete chapter plan. Every candidate group appears once.")
