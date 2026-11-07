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
    expected_page_count: int
    run_created_at: float
    title: str
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
    html_key: str
    structural_quality: dict[str, Any]
