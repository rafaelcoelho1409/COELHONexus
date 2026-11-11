"""Validated support assessment for a synthesized YCS answer."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CriticAssessment(BaseModel):
    confidence_score: float = Field(ge = 0.0, le = 1.0)
    claims_supported: bool
    reasoning: str = Field(max_length = 500)
