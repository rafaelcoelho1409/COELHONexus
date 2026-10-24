"""llm_usage entities — the parsed usage of one LLM call."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class Usage:
    """Model id (None when the response names none) and the token counts of one call."""
    model:            str | None
    tokens_in:        int
    tokens_out:       int
    reasoning_tokens: int
