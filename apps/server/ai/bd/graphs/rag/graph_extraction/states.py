"""Transient LangGraph state for one bounded source-section extraction."""
from __future__ import annotations

from typing import TypedDict


class GraphExtractionState(TypedDict, total = False):
    section_title: str
    source_material: str
    source_evidence: list[str]
    candidate_extraction: dict
    accepted_extraction: dict
    validation_issues: list[str]
    attempt: int
