"""chapter_assign — Pydantic value objects. Decoded directly via
`chat_structured_async(prompt, DocAssignment)` — no manual
response_format dict needed."""
from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator


class ChapterScore(BaseModel):
    """One score for one chapter."""
    # Groq strict json_schema mode requires additionalProperties:false on every
    # object, including nested $defs — extra="forbid" makes model_json_schema()
    # emit it for this model's own schema entry.
    model_config = ConfigDict(extra = "forbid")

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
    model_config = ConfigDict(extra = "forbid")

    scores: list[ChapterScore] = Field(
        description = (
            "ONE score entry per chapter proposal (in the same order as "
            "the chapters list shown in the prompt)."
        ),
    )


@lru_cache(maxsize = 32)
def fixed_key_model(n: int) -> type[BaseModel]:
    """ONE required float per chapter (`c0` .. `c{n-1}`) instead of a list of {chapter_idx, confidence}.
    On the 28-chapter langfuse plan the list came back cut short for 70% of pages (usually after 16 entries;
    only 30% complete) while this form was complete for 100% of pages, 36% faster per call (10.8 s vs 16.8 s):
    a missing key is a validation error the model cannot silently commit."""
    fields = {f"c{i}": (float, Field(description = f"confidence 0.0-1.0 for chapter {i}")) for i in range(n)}
    return create_model("FixedKeyAssignment", __config__ = ConfigDict(extra = "forbid"), **fields)


def scores_from_fixed(obj: BaseModel, n: int) -> DocAssignment:
    """The fixed-key answer as the usual DocAssignment (clamped by ChapterScore)."""
    return DocAssignment(scores = [ChapterScore(chapter_idx = i, confidence = float(getattr(obj, f"c{i}"))) for i in range(n)])
