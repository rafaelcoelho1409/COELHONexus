"""Typed MCP input and output contracts for BD retrieval."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RetrievalStatus = Literal[
    "complete",
    "no_results",
    "disabled",
    "invalid_query",
    "unavailable",
    "timeout",
]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    id: str = Field(min_length = 1, max_length = 240)
    text: str = Field(max_length = 3000)
    score: float = 0.0
    source: str = Field(min_length = 1, max_length = 80)
    citation: dict[str, Any] = Field(default_factory = dict)
    matched_sources: list[str] = Field(default_factory = list)


class RetrievalResult(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    status: RetrievalStatus
    source: str = Field(min_length = 1, max_length = 80)
    query: str = Field(max_length = 1000)
    evidence: list[EvidenceRecord] = Field(default_factory = list, max_length = 8)
    error_type: str | None = Field(default = None, max_length = 100)
