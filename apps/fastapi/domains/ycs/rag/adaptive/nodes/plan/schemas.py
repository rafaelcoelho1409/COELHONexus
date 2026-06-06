"""ycs/rag/adaptive/nodes/plan — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L38-40`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    sub_questions: list[str] = Field(description = "3-8 focused sub-questions")
    strategy:      str = Field(description = "Brief research strategy")
