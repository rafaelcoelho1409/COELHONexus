"""ycs/rag/standard/nodes/corroborate — structured-output Pydantic.

Three-way verdict, not a bool — CRAG's "Ambiguous" action (Yan et al.
2024) is a genuine third state, not a coin flip between corroborate/
contradict. `unclear` is the honest answer when the web search didn't
turn up anything decisive either way, and the caller (`node.py`) skips
appending a note for that verdict rather than manufacturing a false
signal."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CorroborationResult(BaseModel):
    """Verdict on whether external web results support or conflict
    with an answer the grounding judge couldn't verify against the
    corpus."""
    verdict: Literal["corroborates", "contradicts", "unclear"] = Field(
        description = (
            "'corroborates' if the web results support the answer's "
            "claims, 'contradicts' if they conflict with it, "
            "'unclear' if the web results don't decisively confirm "
            "or deny it"
        ),
    )
    note: str = Field(
        description = (
            "One short sentence to append to the answer, written for "
            "the end user (e.g. 'A quick web check supports this.' or "
            "'A quick web check found conflicting information — "
            "verify independently.'). Empty string if verdict is "
            "'unclear' and there's nothing worth telling the user."
        ),
    )
