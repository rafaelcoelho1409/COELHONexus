"""Typed state shared by Book Distiller ingestion graph nodes."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class PageReference(TypedDict):
    """Small manifest reference to one immutable raw Markdown page."""

    page: int
    key: str
    pictures: NotRequired[int]


class IngestionPageReference(TypedDict):
    """Pinned page inputs and output locations; never contains Markdown text."""

    page: int
    source_key: str
    source_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    pictures: NotRequired[int]


class IngestionPageResult(TypedDict, total = False):
    """Compact page result safe to keep in graph checkpoints."""

    page: int
    source_sha256: str
    normalized_sha256: str
    assessment_sha256: str
    patch_proposals_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    assessment: dict[str, object]
    patch_proposal_count: int
    auto_applied_issue_ids: list[str]
    review_required_issue_ids: list[str]
    delivery_disposition: str
    repair_context_chars: int
    repair_call_count: int
    status: str
    error: str


class IngestionPageAuditResult(TypedDict):
    page: int
    source_sha256: str
    markdown_chars: int
    pictures: int
    assessment: dict[str, object]
    warnings: NotRequired[list[str]]
    audit_mode: NotRequired[str]
    structured_output_fallback: NotRequired[bool]


class PageInventoryResult(TypedDict):
    page: int
    key: str
    source_sha256: str
    markdown_chars: int
    pictures: int
    assessment: dict[str, object]


class IngestionInventoryState(TypedDict, total = False):
    """Graph state contains object references and compact results, never page text."""

    book_id: str
    extraction_run_id: str
    extraction_status: str
    expected_page_count: int
    page_refs: list[PageReference]
    inventory: list[PageInventoryResult]
    inventory_summary: dict[str, object]
    status: str


class PageAuditState(TypedDict, total = False):
    """Single-page audit state contains references and evidence, never full Markdown."""

    book_id: str
    extraction_run_id: str
    extraction_status: str
    page_ref: PageReference
    assessment: dict[str, object]
    source_sha256: str
    status: str
    warnings: list[str]


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
    source_page_refs: list[PageReference]
    page_refs: list[IngestionPageReference]
    page_audits: list[IngestionPageAuditResult]
    work_items: list[dict[str, object]]
    page_results: list[IngestionPageResult]
    source_metadata: dict[str, object]
    metadata_proposal: dict[str, object]
    book_metadata: dict[str, object]
    book_metadata_sha256: str
    quality_report: dict[str, object]
    quality_report_sha256: str
    quality_status: str
    status: str
    error: str
