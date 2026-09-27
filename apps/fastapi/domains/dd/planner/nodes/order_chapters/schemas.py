"""order_chapters — Pydantic schema for the LLM ordering response."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChapterOrder(BaseModel):
    """LLM output — a permutation of chapter indices in pedagogical
    order. Permutation validity (right length, no dupes/out-of-range)
    is checked separately in `domain.is_valid_permutation` — the schema
    only constrains the JSON shape, not the semantic property."""
    model_config = ConfigDict(extra = "forbid")

    order: list[int] = Field(
        description = (
            "Permutation of ALL chapter indices, simple-to-complex "
            "pedagogical order."
        ),
    )
    rationale: str = Field(description = "1-2 sentences explaining the order.")
