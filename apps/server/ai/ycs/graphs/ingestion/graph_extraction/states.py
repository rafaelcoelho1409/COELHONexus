"""Typed transient state for one transcript graph extraction."""
from __future__ import annotations

from typing import TypedDict


class GraphExtractionState(TypedDict, total = False):
    transcript: str
    metadata: dict[str, str]
    candidate_extraction: dict
    accepted_extraction: dict
    validation_issues: list[str]
    attempt: int
