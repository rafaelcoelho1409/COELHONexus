"""HTTP shapes of Book Distiller ingestion — the contract the generated web client is built from."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class StartResponse(BaseModel):
    status: Literal["queued", "cached", "locked"]
    book_id: str
    run_id: str | None
    resumed: bool


class BookRow(BaseModel):
    book_id: str
    title: str
    filename: str
    size_bytes: int
    n_pages: int
    pages_done: int
    status: str
    created_at: float
    updated_at: float


class OutlineEntry(BaseModel):
    level: int
    title: str
    page: int


class ChapterRange(BaseModel):
    title: str
    first_page: int
    last_page: int


class PageEntry(BaseModel):
    page: int
    key: str
    chars: int
    headings: int
    tables: int
    pictures: int
    formulas: int
    figures: list[str]
    section_path: list[str]


class Manifest(BaseModel):
    book_id: str
    title: str
    filename: str
    size_bytes: int
    n_pages: int
    status: str
    error: str | None
    outline: list[OutlineEntry]
    chapters: list[ChapterRange]
    pages: list[PageEntry]
    created_at: float
    updated_at: float


class Progress(BaseModel):
    run_id: str
    status: str
    completed: int
    total: int
    error: str | None
    started_at: float
    updated_at: float


class AgenticRunStart(BaseModel):
    status: Literal["queued"]
    book_id: str
    extraction_run_id: str
    agentic_run_id: str


class AgenticRunStatus(BaseModel):
    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    expected_page_count: int
    published_page_count: int
    quality_status: Literal["complete", "complete_with_review"] | None = None
    delivered_page_count: int = 0
    discarded_blank_page_count: int = 0
    review_page_count: int = 0
    review_pages: list[int] = []
    unverified_page_count: int = 0
    unverified_pages: list[int] = []
    discarded_pages: list[int] = []
    image_only_pages: list[int] = []
    progress: AgenticProgress
    created_at: float
    updated_at: float
    finished_at: float | None
    error: str | None


class AgenticProgress(BaseModel):
    current_node: str | None
    node_status: dict[str, str]
    audited_pages: list[int]
    processed_pages: list[int]
    active_audit_pages: list[int]
    active_repair_pages: list[int]


class Snapshot(BaseModel):
    manifest: Manifest
    progress: Progress | None
    agentic: AgenticRunStatus | None = None


class ActiveRun(BaseModel):
    book_id: str
    run_id: str
    progress: Progress


class ActiveRuns(BaseModel):
    active: list[ActiveRun]


class PageBody(BaseModel):
    book_id: str
    page: int
    markdown: str
    content_source: Literal["docling", "agentic"] = "docling"


class PatchSuggestion(BaseModel):
    target_text: str
    replacement: str
    rationale: str
    confidence: float
    risk: str


class PageIssueView(BaseModel):
    issue_id: str
    code: str
    severity: str
    description: str
    evidence: list[str]
    resolution: Literal["auto_applied", "review", "none"]
    proposal: PatchSuggestion | None = None


class PageQuality(BaseModel):
    """What the current Agentic AI run found on one page and what it did about it."""

    book_id: str
    agentic_run_id: str
    page: int
    page_kind: str
    status: Literal["done", "review_required"]
    delivery_disposition: Literal["include", "discarded_blank", "image_only"]
    issues: list[PageIssueView]
    mechanical_fixes: dict[str, int]
    audit_warnings: list[str]
    errors: list[str]


class CancelResponse(BaseModel):
    book_id: str
    status: str


class AgenticRunCancelResponse(BaseModel):
    book_id: str
    agentic_run_id: str
    status: str


class AgenticRunWipeResponse(BaseModel):
    book_id: str
    agentic_run_id: str
    wiped: bool
    objects_deleted: int


class ExtractionWipeResponse(BaseModel):
    book_id: str
    wiped: bool
    objects_deleted: int


class DeleteResponse(BaseModel):
    book_id: str
    deleted: bool
    reason: str | None = None
    objects: int | None = None
