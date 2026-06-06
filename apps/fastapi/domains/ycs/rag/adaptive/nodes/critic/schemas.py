"""ycs/rag/adaptive/nodes/critic — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L25-35`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class CriticAssessment(BaseModel):
    """Output of the critic node."""
    confidence_score: float = Field(
        description = "Confidence in the synthesis quality (0.0-1.0)",
    )
    claims_supported: bool = Field(
        description = (
            "True if all claims in the synthesis are supported by "
            "subagent evidence"
        ),
    )
    reasoning: str = Field(description = "Brief explanation of the assessment")
