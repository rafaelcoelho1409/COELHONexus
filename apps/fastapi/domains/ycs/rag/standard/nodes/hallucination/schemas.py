"""ycs/rag/standard/nodes/hallucination — structured-output Pydantic.

Direct port of deprecated `schemas/youtube/agents.py:L43-53`. Two
booleans — `grounded` (no fabricated facts) AND `addresses_question`
(answer actually responds to the prompt). The graph routing AND's the
two together (deprecated `rag.py:L134`)."""
from __future__ import annotations

from pydantic import BaseModel, Field


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
