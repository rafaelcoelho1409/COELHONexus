"""What the model sends `score_outline`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class OutlineChapter(BaseModel):
    """A chapter of the outline being scored; a draft may be incomplete, so nothing here is validated."""

    title: str = Field(description = "The chapter's title.")
    description: str = Field(default = "", description = "One sentence on what readers learn.")
    key_concepts: list[str] = Field(default_factory = list, description = "Specific concepts, identifiers or commands.")


class OutlineInput(BaseModel):
    chapters: list[OutlineChapter] = Field(description = "The whole outline, every chapter.")
