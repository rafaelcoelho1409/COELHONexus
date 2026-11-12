"""What the model sends `inspect_section`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SectionRef(BaseModel):
    section_id: str = Field(description = "A section id of the chapter, e.g. 's3'.")
