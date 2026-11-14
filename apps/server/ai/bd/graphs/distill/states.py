"""Compact LangGraph state for resumable Book Distiller reports."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

import domains


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
    title: str
    source_chapters: list[dict[str, Any]]
    source_pages: list[domains.bd.distill.entities.SourcePage]
    unit_refs: list[domains.bd.distill.entities.UnitRef]
    completed_units: Annotated[list[domains.bd.distill.entities.UnitRef], operator.add]
    unit_ref: domains.bd.distill.entities.UnitRef
    unit_batch: list[domains.bd.distill.entities.UnitRef]
    chapter_refs: list[domains.bd.distill.entities.ChapterRef]
    completed_chapters: Annotated[list[domains.bd.distill.entities.ChapterRef], operator.add]
    chapter_ref: domains.bd.distill.entities.ChapterRef
    chapter_batch: list[domains.bd.distill.entities.ChapterRef]
    book_synthesis_key: str
    report_key: str
    report_sha256: str
    html_key: str
    html_sha256: str
    covered_page_count: int
    uncovered_pages: list[int]
    review_required: bool
    structural_quality: dict[str, Any]
