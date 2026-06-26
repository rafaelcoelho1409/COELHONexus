"""ycs/rag/adaptive/nodes/plan — structured-output Pydantic.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchPlan(BaseModel):
    # capped 3-8 → 3-5 (see `classify/schemas.py` header).
    sub_questions: list[str] = Field(description = "3-5 focused sub-questions")
    strategy:      str = Field(description = "Brief research strategy")
