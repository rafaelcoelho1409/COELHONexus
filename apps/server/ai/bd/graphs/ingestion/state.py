"""Typed state shared by Book Distiller ingestion graph nodes."""
from __future__ import annotations
from . import entities

from typing import TypedDict


class IngestionRunState(TypedDict, total = False):
    """Versioned book run state; large source and output text stays in MinIO."""

    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    workflow_version: str
    source_manifest_key: str
    source_manifest_sha256: str
    run_created_at: float
    expected_page_count: int
    source_page_refs: list[entities.PageReference]
    page_refs: list[entities.IngestionPageReference]
    page_audits: list[entities.IngestionPageAuditResult]
    work_items: list[dict[str, object]]
    page_results: list[entities.IngestionPageResult]
    source_metadata: dict[str, object]
    book_metadata: dict[str, object]
    book_metadata_sha256: str
    quality_report: dict[str, object]
    quality_status: str
    status: str
