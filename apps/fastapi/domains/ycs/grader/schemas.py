"""ycs/grader — structured-output Pydantic for the relevance LLM call.

Direct port of deprecated `schemas/youtube/agents.py:L56-60`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class GradeResult(BaseModel):
    """Binary relevance grade for a document."""
    score: str = Field(
        description = (
            "'relevant' if the document answers the question, "
            "'not_relevant' otherwise"
        ),
    )
