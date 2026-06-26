"""ycs/rag/standard/nodes/hallucination — structured-output Pydantic.
Two
booleans — `grounded` (no fabricated facts) AND `addresses_question`
(answer actually responds to the prompt). The graph routing AND's the
two together (deprecated `rag.py:L134`).

2026-06-11: added a string→bool coercer (`field_validator mode="before"`)
because rotator-routed models occasionally emit `"true"` / `"false"`
(strings) instead of literal JSON booleans. Without coercion, Groq's
tool-call validator rejects the response with a 400 and the graph
loops into rewrite — even though the underlying answer was correct.
Accepts the strict bool, the `"true"/"false"` string form, `1/0`, and
`"yes"/"no"` (case-insensitive) for resilience across model styles."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


_TRUTHY = {"true", "1", "yes", "y", "on", True, 1}
_FALSY  = {"false", "0", "no", "n", "off", False, 0}


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        lowered = v.strip().lower()
        if lowered in _TRUTHY: return True
        if lowered in _FALSY:  return False
    if v in _TRUTHY: return True
    if v in _FALSY:  return False
    raise ValueError(f"cannot coerce {v!r} to bool")


class HallucinationCheck(BaseModel):
    """Result of hallucination detection."""
    grounded: bool = Field(
        description = (
            "True if ALL claims in the answer are supported by the source documents"
        ),
    )
    addresses_question: bool = Field(
        description = "True if the answer actually addresses the original question",
    )
    reason: str = Field(description = "Brief explanation of the assessment")

    @field_validator("grounded", "addresses_question", mode = "before")
    @classmethod
    def _coerce_bools(cls, v: Any) -> bool:
        return _coerce_bool(v)
