"""chapter_select — Pydantic value objects for the oversize-chapter split call.
Decoded via `chat_structured_async(prompt, SplitPlan)`."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SplitGroup(BaseModel):
    """One chapter carved out of an oversize chapter."""
    # Groq strict json_schema mode needs additionalProperties:false on every object.
    model_config = ConfigDict(extra = "forbid")

    title: str = Field(description = "Concrete 2-8 word chapter title, unique among all chapters.")
    description: str = Field(description = "ONE sentence: what the reader learns in this chapter.")
    unit_ids: list[int] = Field(description = "Ids of the units (from the list shown) that belong to this chapter.")


class SplitPlan(BaseModel):
    """LLM output: the oversize chapter partitioned into cohesive chapters."""
    model_config = ConfigDict(extra = "forbid")

    groups: list[SplitGroup] = Field(
        description = "The new chapters. Every unit id appears in exactly one group.",
    )
