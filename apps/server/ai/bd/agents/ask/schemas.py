"""Validated model boundaries for the BD Ask agents."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AskPlan(BaseModel):
    mode: Literal["fast", "standard", "deep"]
    sub_questions: list[str] = Field(max_length = 4)
    strategy: str = Field(max_length = 500)


class EvidenceAnswer(BaseModel):
    answer: str = Field(max_length = 12000)
    evidence_ids: list[str] = Field(max_length = 12)


class EvidenceGrades(BaseModel):
    relevant_evidence_ids: list[str] = Field(max_length = 20)
    coverage: Literal["adequate", "partial", "insufficient"]
    reasoning: str = Field(max_length = 500)


class EvidenceAssessment(BaseModel):
    confidence_score: float = Field(ge = 0.0, le = 1.0)
    claims_supported: bool
    reasoning: str = Field(max_length = 500)
