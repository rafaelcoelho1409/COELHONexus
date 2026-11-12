"""Validated result returned by the YCS research planner."""
from __future__ import annotations

import pydantic


class ResearchPlan(pydantic.BaseModel):
    sub_questions: list[str] = pydantic.Field(min_length = 2, max_length = 5)
    strategy: str = pydantic.Field(min_length = 1, max_length = 500)
