"""chapter_assign — Pydantic value objects + LLM response_format spec."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ChapterScore(BaseModel):
    """One score for one chapter."""
    chapter_idx: int = Field(description = "Index into the proposals list.")
    confidence: float = Field(
        description = (
            "0.0-1.0 confidence that this doc belongs to this chapter. "
            "Set 0.0 for chapters with no relevance; set 0.5+ only when "
            "the doc materially supports the chapter."
        ),
    )

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if v < 0.0:
            return 0.0
        if v > 1.0:
            return 1.0
        return float(v)


class DocAssignment(BaseModel):
    """LLM output for ONE doc — confidence against each chapter."""
    scores: list[ChapterScore] = Field(
        description = (
            "ONE score entry per chapter proposal (in the same order as "
            "the chapters list shown in the prompt)."
        ),
    )


ASSIGN_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "doc_assignment",
        "schema": DocAssignment.model_json_schema(),
        "strict": False,
    },
}
