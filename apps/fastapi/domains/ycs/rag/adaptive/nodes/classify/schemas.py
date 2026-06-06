"""ycs/rag/adaptive/nodes/classify — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L7-22`."""
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
        default_factory = list,
        description = "For 'deep' mode: 3-8 focused sub-questions to investigate",
    )
    channel_names: list[str] = Field(
        default_factory = list,
        description = (
            "Channel or person names mentioned in the query "
            "(for scope filtering)"
        ),
    )
