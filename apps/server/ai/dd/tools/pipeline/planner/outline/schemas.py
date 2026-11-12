"""What the model sends `score_outline`."""
from __future__ import annotations

import pydantic


class OutlineChapter(pydantic.BaseModel):
    """A chapter of the outline being scored; a draft may be incomplete, so nothing here is validated."""

    title: str = pydantic.Field(description = "The chapter's title.")
    description: str = pydantic.Field(default = "", description = "One sentence on what readers learn.")
    key_concepts: list[str] = pydantic.Field(default_factory = list, description = "Specific concepts, identifiers or commands.")


class OutlineInput(pydantic.BaseModel):
    chapters: list[OutlineChapter] = pydantic.Field(description = "The whole outline, every chapter.")
