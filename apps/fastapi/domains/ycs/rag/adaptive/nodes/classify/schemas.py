"""ycs/rag/adaptive/nodes/classify — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L7-22`.

2026-06-15 — `sub_questions` and `channel_names` no longer carry
`default_factory=list`: Groq's strict `response_format` validator
requires every property listed in `properties` to also appear in
`required`, and Pydantic only emits a field as required when it has
NO default. The previous defaults made Groq reject every classify
call with `BadRequestError: 'required' is required to be supplied
and to be an array including every key in properties`, burning one
rotator retry per request.

Also caps `sub_questions` at 3-5 (was 3-8). Empirically 8 sub-
questions made the DEEP fan-out run ~3 waves at cap=3, blowing past
the user-tolerance window. Synthesis already deduplicates themes, so
shrinking the plan trades modest coverage for ~40% faster wall-
time."""
from __future__ import annotations

from pydantic import BaseModel, Field


class QueryClassification(BaseModel):
    """Output of the query classifier."""
    mode: str = Field(
        description = (
            "Query mode: 'fast' for simple factual, 'standard' for "
            "evidence-based, 'deep' for analytical"
        ),
    )
    reasoning: str = Field(
        description = "Brief explanation of why this mode was chosen",
    )
    sub_questions: list[str] = Field(
        description = (
            "For 'deep' mode: 3-5 focused sub-questions to investigate. "
            "Return [] for 'fast' or 'standard' mode."
        ),
    )
    channel_names: list[str] = Field(
        description = (
            "Channel or person names mentioned in the query "
            "(for scope filtering). Return [] if none."
        ),
    )
