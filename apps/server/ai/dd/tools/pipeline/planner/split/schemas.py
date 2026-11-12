"""What the model sends `cluster_units` and `validate_split`."""
from __future__ import annotations

import pydantic


class ClusterInput(pydantic.BaseModel):
    n_parts: int = pydantic.Field(description = "How many chapters to carve the units into.")


class SplitGroupInput(pydantic.BaseModel):
    """One proposed chapter of the split; checked by `validate_split`, so nothing here is validated."""

    title: str = pydantic.Field(description = "The chapter's title.")
    description: str = pydantic.Field(default = "", description = "One sentence on what readers learn.")
    unit_ids: list[int] = pydantic.Field(description = "Ids of the units that belong to the chapter.")


class ValidateInput(pydantic.BaseModel):
    groups: list[SplitGroupInput] = pydantic.Field(description = "Every proposed chapter; each unit id appears in exactly one.")
