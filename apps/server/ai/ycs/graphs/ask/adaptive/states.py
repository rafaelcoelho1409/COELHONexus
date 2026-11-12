"""Typed state owned by the YCS adaptive Agentic RAG graph."""
from __future__ import annotations

import operator
import typing

import langchain_core.documents


class AdaptiveAskState(typing.TypedDict, total = False):
    question: str
    thread_id: str
    route: str
    contextualized: bool
    intent: str
    mode: str
    force_mode: str
    generation: str
    documents: list[langchain_core.documents.Document]
    pre_grade_documents: list[langchain_core.documents.Document]
    citations: list[dict]
    grounded: bool
    retrieval_sources: list[str]
    retry_count: int
    search_query: str
    conversation_history: list[dict]
    channel_ids: list[str]
    skipped_arms: list[str]
    arm_empty_streaks: dict[str, int]
    sub_questions: list[str]
    sub_results: typing.Annotated[list[dict], operator.add]
    research_plan: str
    confidence_score: float
