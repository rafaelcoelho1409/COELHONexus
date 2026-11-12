"""What the steps of the checklist subgraph hand to each other."""
from __future__ import annotations

from typing import TypedDict


class JudgeRun(TypedDict):
    """One judge run: the five LLM verdicts (as criterion dicts), whether the judge answered at all, and what the Rotator told about the call."""

    results: list[dict]
    answered: bool
    repaired: bool
    wall_ms: int
    meta: dict
