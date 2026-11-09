"""Structured answer for grouping the candidate page clusters."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DesignedChapter(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: str = Field(description = "Concrete topic title, not a generic content type.")
    description: str = Field(description = "One sentence describing what readers learn.")
    objective: str = Field(description = "A measurable ability the reader has after this chapter.")
    group_ids: list[str] = Field(
        description = "Assigned candidate group ids; every group appears exactly once across all chapters.",
    )
    key_concepts: list[str] = Field(default_factory = list)
    prerequisites: list[str] = Field(default_factory = list, description = "Titles of chapters that should come first.")
    kind: Literal["tutorial", "appendix"] = "tutorial"
    capstone_ability: bool = Field(description = "Whether the chapter's best code examples can compose into one practical application.")
    capstone_reason: str = Field(default = "", description = "Short reason for the capability decision.")
    synth_eligible: bool = Field(
        default = True,
        description = "Whether this chapter should be synthesized into the main learning tutorial; lookup-only material may be excluded.",
    )
    synth_exclusion_reason: str = Field(
        default = "",
        description = "Required when synth_eligible is false; explain why this material is unnecessary in the tutorial while remaining available as source reference.",
    )

    @field_validator("group_ids", mode = "before")
    @classmethod
    def _clean_group_ids(cls, value):
        if not isinstance(value, list):
            return []
        # Preserve duplicates so assignment validation can report them instead of silently repairing them.
        out = [" ".join(str(item or "").split()) for item in value]
        return [item for item in out if item]

    @field_validator("key_concepts", "prerequisites", mode = "before")
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


class ChapterDesignAnswer(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    candidate_partition_index: int = Field(
        description = "Zero-based index of the selected candidate partition in the supplied candidate_partitions list; required for a valid design.",
    )
    chapters: list[DesignedChapter] = Field(description = "The complete chapter plan. Every candidate group appears once.")
