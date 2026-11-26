"""Compact LangGraph state for resumable Book Distiller reports."""
from __future__ import annotations
import domains

import operator
from typing import Annotated, Any, TypedDict


class DistillRunState(TypedDict, total = False):
    book_id: str
    run_id: str
    extraction_run_id: str
    ingestion_ai_run_id: str
    source_manifest_key: str
    source_manifest_sha256: str
    ingestion_ai_manifest_key: str
    ingestion_ai_manifest_sha256: str
    content_selection_key: str
    content_selection_sha256: str
    content_selection_revision: int
    processed_book_key: str
    processed_book_sha256: str
    processed_book_revision: int
    processed_page_count: int
    expected_page_count: int
    selected_page_count: int
    run_created_at: float
    resume_from_node: str | None
    replay_target_node: str | None
    title: str
    source_chapters: list[dict[str, Any]]
    chapter_mapping_notes: list[str]
    source_pages: list[domains.bd.distill.ai.entities.SourcePage]
    unit_refs: list[domains.bd.distill.ai.entities.UnitRef]
    extracted_units: Annotated[list[domains.bd.distill.ai.entities.UnitRef], operator.add]
    completed_units: Annotated[list[domains.bd.distill.ai.entities.UnitRef], operator.add]
    unit_ref: domains.bd.distill.ai.entities.UnitRef
    unit_batch: list[domains.bd.distill.ai.entities.UnitRef]
    chapter_refs: list[domains.bd.distill.ai.entities.ChapterRef]
    report_chapters: list[domains.bd.distill.ai.entities.ChapterRef]
    synthesized_chapters: Annotated[list[domains.bd.distill.ai.entities.ChapterRef], operator.add]
    reviewed_chapters: Annotated[list[domains.bd.distill.ai.entities.ChapterRef], operator.add]
    completed_chapters: Annotated[list[domains.bd.distill.ai.entities.ChapterRef], operator.add]
    chapter_ref: domains.bd.distill.ai.entities.ChapterRef
    chapter_batch: list[domains.bd.distill.ai.entities.ChapterRef]
    book_synthesis_key: str
    query: str
    include_web: bool
    retrieval_sources_to_run: list[str]
    elasticsearch_result: dict[str, Any]
    qdrant_result: dict[str, Any]
    neo4j_result: dict[str, Any]
    web_search_result: dict[str, Any]
    evidence: list[dict[str, Any]]
    source_status: dict[str, str]
    source_queries: dict[str, list[str]]
    decision_brief_key: str
    research_context_key: str
    report_key: str
    report_sha256: str
    html_key: str
    html_sha256: str
    covered_page_count: int
    uncovered_pages: list[int]
    review_required: bool
    structural_quality: dict[str, Any]
