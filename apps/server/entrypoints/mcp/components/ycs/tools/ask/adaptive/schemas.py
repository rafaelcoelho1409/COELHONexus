"""Structured output schema for YCS Ask MCP calls."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AskMode = Literal["fast", "standard", "deep"]


class AskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application: Literal["ycs"] = "ycs"
    answer: str
    mode: Literal["fast", "standard", "deep"]
    citations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    grounded: bool = False
    retrieval_sources: list[str] = Field(default_factory=list, max_length=20)
    retry_count: int = Field(default=0, ge=0)
    search_query: str = ""
    sub_questions: list[str] = Field(default_factory=list, max_length=5)
    confidence_score: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int = Field(ge=0)
    usage: dict[str, Any] = Field(default_factory=dict)
