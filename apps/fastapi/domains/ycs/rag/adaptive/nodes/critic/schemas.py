"""ycs/rag/adaptive/nodes/critic — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L25-35`.

2026-06-11: bool coercion mirrors the hallucination-check schema for
the same reason — rotator-routed models occasionally emit `"true"` /
`"false"` strings instead of JSON booleans, and a strict bool field
makes Groq's tool-call validator reject the response. See
`standard/nodes/hallucination/schemas.py` for the canonical helper."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from domains.ycs.rag.standard.nodes.hallucination.schemas import (
    _coerce_bool,
)


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

    @field_validator("claims_supported", mode = "before")
    @classmethod
    def _coerce_bool_field(cls, v: Any) -> bool:
        return _coerce_bool(v)
