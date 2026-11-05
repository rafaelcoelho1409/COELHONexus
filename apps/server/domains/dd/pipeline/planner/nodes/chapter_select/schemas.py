"""chapter_select — Pydantic value objects for the oversize-chapter split call.
Decoded via `chat_structured_async(prompt, SplitPlan)`."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SplitGroup(BaseModel):
    """One chapter carved out of an oversize chapter."""
    # Groq strict json_schema mode needs additionalProperties:false on every object.
    model_config = ConfigDict(extra = "forbid")

    title: str = Field(description = "Concrete 1-8 word chapter title, unique among all chapters.")
    description: str = Field(description = "ONE sentence: what the reader learns in this chapter.")
    unit_ids: list[int] = Field(description = "Ids of the units (from the list shown) that belong to this chapter.")


class SplitPlan(BaseModel):
    """LLM output: the oversize chapter partitioned into cohesive chapters."""
    model_config = ConfigDict(extra = "forbid")

    groups: list[SplitGroup] = Field(
        description = "The new chapters. Every unit id appears in exactly one group.",
    )


class GroupName(BaseModel):
    """Title + description for one already-computed group of pages."""
    model_config = ConfigDict(extra = "forbid")

    id: int = Field(description = "The group id exactly as shown.")
    title: str = Field(description = "Concrete 1-8 word chapter title, unique among all chapters.")
    description: str = Field(description = "ONE sentence: what the reader learns in this chapter.")


class GroupNames(BaseModel):
    """LLM output: one name per group (the partition itself is computed, not asked for)."""
    model_config = ConfigDict(extra = "forbid")

    groups: list[GroupName] = Field(description = "Exactly one entry per group id shown.")
