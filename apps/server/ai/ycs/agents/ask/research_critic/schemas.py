"""Validated support assessment for a synthesized YCS answer."""
from __future__ import annotations

import pydantic


class CriticAssessment(pydantic.BaseModel):
    confidence_score: float = pydantic.Field(ge = 0.0, le = 1.0)
    claims_supported: bool
    reasoning: str = pydantic.Field(max_length = 500)
