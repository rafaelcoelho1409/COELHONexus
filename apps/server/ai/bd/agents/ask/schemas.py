"""Validated model boundaries for the BD Ask agents."""
from __future__ import annotations

import typing

import pydantic


class AskPlan(pydantic.BaseModel):
    mode: typing.Literal["fast", "standard", "deep"]
    sub_questions: list[str] = pydantic.Field(max_length = 4)
    strategy: str = pydantic.Field(max_length = 500)


class EvidenceAnswer(pydantic.BaseModel):
    answer: str = pydantic.Field(max_length = 12000)
    evidence_ids: list[str] = pydantic.Field(max_length = 12)


class EvidenceGrades(pydantic.BaseModel):
    relevant_evidence_ids: list[str] = pydantic.Field(max_length = 20)
    coverage: typing.Literal["adequate", "partial", "insufficient"]
    reasoning: str = pydantic.Field(max_length = 500)


class EvidenceAssessment(pydantic.BaseModel):
    confidence_score: float = pydantic.Field(ge = 0.0, le = 1.0)
    claims_supported: bool
    reasoning: str = pydantic.Field(max_length = 500)
