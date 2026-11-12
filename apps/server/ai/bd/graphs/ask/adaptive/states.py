"""Transient, book-scoped state for one adaptive BD Ask run."""
from __future__ import annotations

import typing


class AdaptiveAskState(typing.TypedDict, total = False):
    question: str
    thread_id: str
    conversation_history: list[dict]
    force_mode: str
    supplied_sub_questions: list[str]
    mode: str
    research_plan: str
    sub_questions: list[str]
    queries: list[str]
    documents: list[dict]
    document_count: int
    retrieval_sources: list[str]
    retrieval_sufficient: bool
    retrieval_coverage: str
    retrieval_error: str
    retry_count: int
    generation: str
    citations: list[dict]
    evidence_ids: list[str]
    grounded: bool
    confidence_score: float
    verification_status: str
    sub_results: list[dict]
