"""What the model sends `inspect_section`."""
from __future__ import annotations

import pydantic


class SectionRef(pydantic.BaseModel):
    section_id: str = pydantic.Field(description = "A section id of the chapter, e.g. 's3'.")
