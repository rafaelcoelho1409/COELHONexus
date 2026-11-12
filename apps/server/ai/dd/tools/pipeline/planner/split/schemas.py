"""What the model sends `cluster_units` and `validate_split`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ClusterInput(BaseModel):
    n_parts: int = Field(description = "How many chapters to carve the units into.")


class SplitGroupInput(BaseModel):
    """One proposed chapter of the split; checked by `validate_split`, so nothing here is validated."""

    title: str = Field(description = "The chapter's title.")
    description: str = Field(default = "", description = "One sentence on what readers learn.")
    unit_ids: list[int] = Field(description = "Ids of the units that belong to the chapter.")


class ValidateInput(BaseModel):
    groups: list[SplitGroupInput] = Field(description = "Every proposed chapter; each unit id appears in exactly one.")
