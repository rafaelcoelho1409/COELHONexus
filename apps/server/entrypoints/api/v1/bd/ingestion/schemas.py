"""HTTP shapes of Book Distiller ingestion — the contract the generated web client is built from."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    quality_review_reasons: dict[str, int] = Field(default_factory = dict)
    visual_failed_count: int = 0
    visual_failed_pages: list[int] = Field(default_factory = list)
    delivered_page_count: int = 0
    discarded_blank_page_count: int = 0
    selected_page_count: int = 0
    excluded_page_count: int = 0
    content_selection_revision: int | None = None
    processed_page_count: int = 0
    processed_book_revision: int | None = None
    contents_status: Literal["complete", "needs_review", "unavailable"] | None = None
    review_page_count: int = 0
    review_pages: list[int] = []
    content_selection_review_count: int = 0
    content_selection_review_pages: list[int] = []
    unverified_page_count: int = 0
    unverified_pages: list[int] = []
    discarded_pages: list[int] = []
    image_only_pages: list[int] = []
    progress: AgenticProgress
    created_at: float
    updated_at: float
    finished_at: float | None
    error: str | None


class RetrievalIndexStatus(BaseModel):
    workflow_version: str
    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    processed_book_revision: int
    processed_page_count: int
    status: Literal["queued", "running", "done", "failed", "cancelled"]
    cancel_requested: bool = False
    current_node: str | None
    node_status: dict[str, str]
    attempt: int
    elasticsearch_index: str | None = None
    elasticsearch_document_count: int = 0
    elasticsearch_passage_index: str | None = None
    elasticsearch_passage_count: int = 0
    pin_sources_pages_completed: int = 0
    elasticsearch_pages_completed: int = 0
    qdrant_collection: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    qdrant_point_count: int = 0
    qdrant_pages_completed: int = 0
    neo4j_node_count: int = 0
    neo4j_relationship_count: int = 0
    graph_batches_completed: int = 0
    graph_batches_total: int = 0
    neo4j_pages_completed: int = 0
    neo4j_llm_calls_active: int | None = None
    neo4j_stage: Literal[
        "idle",
        "extracting",
        "building_graph",
        "clearing_graph",
        "preparing_writes",
        "writing_nodes",
        "writing_relationships",
        "complete",
    ] = "idle"
    neo4j_stage_completed: int = 0
    neo4j_stage_total: int = 0
    neo4j_node_write_batches_completed: int = 0
    neo4j_node_write_batches_total: int = 0
    neo4j_relationship_write_batches_completed: int = 0
    neo4j_relationship_write_batches_total: int = 0
    created_at: float
    updated_at: float
    finished_at: float | None = None
    error: str | None = None


class RetrievalIndexWipeResponse(BaseModel):
    book_id: str
    agentic_run_id: str
    wiped: bool
    objects_deleted: int


class AgenticNodePageProgress(BaseModel):
    completed_count: int = 0
    total_count: int = 0
    active_count: int = 0


class AgenticProgress(BaseModel):
    current_node: str | None
    node_status: dict[str, str]
    audited_pages: list[int]
    processed_pages: list[int]
    repaired_pages: list[int] = Field(default_factory = list)
    active_audit_pages: list[int]
    active_repair_pages: list[int]
    classified_pages: list[int] = Field(default_factory = list)
    active_classification_pages: list[int] = Field(default_factory = list)
    visual_candidate_count: int = 0
    visual_completed_count: int = 0
    visual_converted_count: int = 0
    visual_failed_count: int = 0
    node_page_progress: dict[str, AgenticNodePageProgress] = Field(default_factory = dict)
    selected_page_count: int = 0
    excluded_page_count: int = 0


class ContentSelectionPage(BaseModel):
    page_number: int
    role: str
    confidence: float
    rationale: str
    disposition: Literal["include", "exclude"]
    decision_source: str


class ContentSelectionResponse(BaseModel):
    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    classifier_version: str
    revision: int
    expected_page_count: int
    included_page_count: int
    excluded_page_count: int
    sha256: str
    pages: list[ContentSelectionPage]


class ContentSelectionOverride(BaseModel):
    page_number: int
    disposition: Literal["include", "exclude"]


class ContentSelectionUpdate(BaseModel):
    expected_revision: int = Field(gt = 0, strict = True)
    overrides: list[ContentSelectionOverride]


class ProcessedBookPage(BaseModel):
    processed_page_number: int
    source_page_number: int
    section_path: list[str]
    role: str


class ProcessedContentsEntry(BaseModel):
    entry_id: str = ""
    parent_entry_id: str | None = None
    level: int
    title: str
    processed_page_number: int
    source_page_number: int
    confidence: float
    review_required: bool
    provenance: Literal[
        "source_outline",
        "processed_page_heading",
        "section_path",
        "source_outline_and_page_heading",
        "source_outline_and_section_path",
    ] = "source_outline"
    source_outline_index: int | None = None


class ProcessedBookResponse(BaseModel):
    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    selection_revision: int
    expected_source_page_count: int
    source_page_count: int
    contents_status: Literal["complete", "needs_review", "unavailable"]
    contents: list[ProcessedContentsEntry]
    pages: list[ProcessedBookPage]


class Snapshot(BaseModel):
    manifest: Manifest
    progress: Progress | None
    agentic: AgenticRunStatus | None = None
    retrieval_index: RetrievalIndexStatus | None = None


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
    converted_figures: list[str] = Field(default_factory = list)


class PatchSuggestion(BaseModel):
    target_text: str
    replacement: str
    rationale: str
    confidence: float
    risk: str
    verification_status: str | None = None
    verification_reason: str | None = None


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
