"""Validated result returned by the YCS research planner."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    sub_questions: list[str] = Field(min_length = 2, max_length = 5)
    strategy: str = Field(min_length = 1, max_length = 500)
