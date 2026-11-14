"""Validated contracts for persisted Book Distiller AI ingestion runs."""
from __future__ import annotations
from . import versions

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator


SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
ObjectKey = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


class AgenticRunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgenticPageStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class AgenticNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class AgenticRunProgress(BaseModel):
    """Small, durable progress snapshot for the live Ingestion view."""

    current_node: str | None = None
    node_status: dict[str, AgenticNodeStatus] = Field(default_factory = dict)
    audited_pages: list[int] = Field(default_factory = list)
    audit_warning_pages: list[int] = Field(default_factory = list)
    processed_pages: list[int] = Field(default_factory = list)
    active_audit_pages: list[int] = Field(default_factory = list)
    active_repair_pages: list[int] = Field(default_factory = list)
    classified_pages: list[int] = Field(default_factory = list)
    active_classification_pages: list[int] = Field(default_factory = list)


class ContentSelectionPage(BaseModel):
    """Classifier result and effective inclusion policy for one original physical page."""

    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0, strict = True)
    role: Literal["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]
    confidence: float = Field(ge = 0, le = 1, allow_inf_nan = False)
    rationale: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 600)]
    disposition: Literal["include", "exclude"]
    decision_source: Literal["classifier", "content_policy", "conservative_fallback", "user"]


class ContentSelectionManifest(BaseModel):
    """Append-only page-selection revision, independent from canonical source and QA artifacts."""

    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0"] = "1.0"
    classifier_version: NonEmptyText
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    agentic_run_id: SafeSegment
    revision: int = Field(gt = 0, strict = True)
    expected_page_count: int = Field(gt = 0, strict = True)
    pages: list[ContentSelectionPage]
    created_at: float = Field(gt = 0, allow_inf_nan = False)

    @model_validator(mode = "after")
    def _validate_selection(self) -> ContentSelectionManifest:
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, self.expected_page_count + 1)):
            raise ValueError("content selection must include every physical page in order")
        return self


class ProcessedBookPage(BaseModel):
    """A selected, densely numbered page with its immutable physical source identity."""

    model_config = ConfigDict(extra = "forbid")

    processed_page_number: int = Field(gt = 0, strict = True)
    source_page_number: int = Field(gt = 0, strict = True)
    source_key: ObjectKey
    source_sha256: Sha256Hex
    content_key: ObjectKey
    content_sha256: Sha256Hex
    section_path: list[str] = Field(default_factory = list)
    role: Literal["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]


class ProcessedContentsEntry(BaseModel):
    """One reconciled contents item; source page identity is retained for citations and review."""

    model_config = ConfigDict(extra = "forbid")

    level: int = Field(ge = 1, le = 8, strict = True)
    title: NonEmptyText = Field(max_length = 300)
    processed_page_number: int = Field(gt = 0, strict = True)
    source_page_number: int = Field(gt = 0, strict = True)
    confidence: float = Field(ge = 0, le = 1, allow_inf_nan = False)
    review_required: bool = False


class ProcessedBookManifest(BaseModel):
    """Canonical processed book page set pinned to one content-selection revision."""

    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0"] = "1.0"
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    agentic_run_id: SafeSegment
    selection_key: ObjectKey
    selection_sha256: Sha256Hex
    selection_revision: int = Field(gt = 0, strict = True)
    expected_source_page_count: int = Field(gt = 0, strict = True)
    source_page_count: int = Field(gt = 0, strict = True)
    pages: list[ProcessedBookPage]
    contents: list[ProcessedContentsEntry] = Field(default_factory = list)
    contents_status: Literal["complete", "needs_review", "unavailable"]
    contents_agent_version: NonEmptyText
    created_at: float = Field(gt = 0, allow_inf_nan = False)

    @model_validator(mode = "after")
    def _validate_processed_book(self) -> ProcessedBookManifest:
        processed_numbers = [page.processed_page_number for page in self.pages]
        source_numbers = [page.source_page_number for page in self.pages]
        if processed_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("processed pages must have contiguous numbers starting at one")
        if source_numbers != sorted(set(source_numbers)):
            raise ValueError("processed page source identities must be unique and ordered")
        if self.source_page_count != len(self.pages):
            raise ValueError("source_page_count must match the processed page set")
        included = set(source_numbers)
        page_map = {page.source_page_number: page.processed_page_number for page in self.pages}
        if any(entry.source_page_number not in included for entry in self.contents):
            raise ValueError("contents entries must point to included source pages")
        if any(page_map[entry.source_page_number] != entry.processed_page_number for entry in self.contents):
            raise ValueError("contents page numbers must match the processed page map")
        return self


class IngestionPageArtifact(BaseModel):
    """Source identity and versioned output references for one physical page."""

    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0, strict = True)
    source_key: ObjectKey
    source_sha256: Sha256Hex
    normalized_key: ObjectKey
    assessment_key: ObjectKey
    patch_proposals_key: ObjectKey
    normalized_sha256: Sha256Hex | None = None
    assessment_sha256: Sha256Hex | None = None
    patch_proposals_sha256: Sha256Hex | None = None
    status: AgenticPageStatus = AgenticPageStatus.PENDING
    delivery_disposition: Literal["include", "discarded_blank", "image_only"] = "include"

    @model_validator(mode = "after")
    def _validate_completed_artifacts(self) -> IngestionPageArtifact:
        if self.status in {AgenticPageStatus.DONE, AgenticPageStatus.REVIEW_REQUIRED}:
            if any(value is None for value in (
                self.normalized_sha256,
                self.assessment_sha256,
                self.patch_proposals_sha256,
            )):
                raise ValueError("completed pages must include hashes for every output artifact")
        return self


class IngestionAIRunManifest(BaseModel):
    """Versioned manifest pinning AI outputs to a specific Docling extraction."""

    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4"] = versions.MANIFEST_SCHEMA_VERSION
    workflow_version: NonEmptyText = versions.WORKFLOW_VERSION
    agentic_run_id: SafeSegment
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    source_manifest_key: ObjectKey
    source_manifest_sha256: Sha256Hex
    status: AgenticRunStatus = AgenticRunStatus.QUEUED
    expected_page_count: int = Field(gt = 0, strict = True)
    progress: AgenticRunProgress = Field(default_factory = AgenticRunProgress)
    pages: list[IngestionPageArtifact] = Field(default_factory = list)
    book_metadata_key: ObjectKey
    book_metadata_sha256: Sha256Hex | None = None
    quality_report_key: ObjectKey
    quality_report_sha256: Sha256Hex | None = None
    quality_status: Literal["complete", "complete_with_review"] | None = None
    delivered_page_count: int = Field(default = 0, ge = 0, strict = True)
    discarded_blank_page_count: int = Field(default = 0, ge = 0, strict = True)
    review_page_count: int = Field(default = 0, ge = 0, strict = True)
    unverified_page_count: int = Field(default = 0, ge = 0, strict = True)
    unverified_pages: list[int] = Field(default_factory = list)
    content_selection_key: ObjectKey | None = None
    content_selection_sha256: Sha256Hex | None = None
    content_selection_revision: int | None = Field(default = None, ge = 1, strict = True)
    selected_page_count: int | None = Field(default = None, ge = 0, strict = True)
    excluded_page_count: int | None = Field(default = None, ge = 0, strict = True)
    processed_book_key: ObjectKey | None = None
    processed_book_sha256: Sha256Hex | None = None
    processed_book_revision: int | None = Field(default = None, ge = 1, strict = True)
    processed_page_count: int | None = Field(default = None, ge = 1, strict = True)
    contents_status: Literal["complete", "needs_review", "unavailable"] | None = None
    created_at: float = Field(gt = 0, allow_inf_nan = False)
    updated_at: float = Field(gt = 0, allow_inf_nan = False)
    finished_at: float | None = Field(default = None, gt = 0, allow_inf_nan = False)
    error: str | None = None

    @model_validator(mode = "after")
    def _validate_run_manifest(self) -> IngestionAIRunManifest:
        page_numbers = [page.page_number for page in self.pages]
        if len(page_numbers) != len(set(page_numbers)):
            raise ValueError("manifest page numbers must be unique")
        if any(page_number > self.expected_page_count for page_number in page_numbers):
            raise ValueError("manifest page numbers cannot exceed expected_page_count")
        if page_numbers != sorted(page_numbers):
            raise ValueError("manifest pages must be stored in physical page order")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")

        terminal = self.status in {
            AgenticRunStatus.DONE,
            AgenticRunStatus.FAILED,
            AgenticRunStatus.CANCELLED,
        }
        if terminal != (self.finished_at is not None):
            raise ValueError("finished_at must be set exactly for terminal run statuses")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        if self.finished_at is not None and self.updated_at < self.finished_at:
            raise ValueError("updated_at cannot precede finished_at")
        if self.status == AgenticRunStatus.FAILED and not self.error:
            raise ValueError("failed runs must include an error")
        if self.status != AgenticRunStatus.FAILED and self.error is not None:
            raise ValueError("error is only valid for failed runs")

        if self.status == AgenticRunStatus.DONE:
            expected_pages = list(range(1, self.expected_page_count + 1))
            if page_numbers != expected_pages:
                raise ValueError("completed runs must reference every physical page in order")
            if any(page.status not in {
                AgenticPageStatus.DONE,
                AgenticPageStatus.REVIEW_REQUIRED,
            } for page in self.pages):
                raise ValueError("completed runs may contain only completed or review-required pages")
            if self.book_metadata_sha256 is None or self.quality_report_sha256 is None:
                raise ValueError("completed runs must include hashes for book metadata and quality report")
            if self.schema_version in {"1.1", "1.2", "1.3", "1.4"}:
                if self.quality_status is None:
                    raise ValueError("completed 1.1 runs must include a quality status")
                if self.delivered_page_count != sum(
                    page.delivery_disposition != "discarded_blank" for page in self.pages
                ):
                    raise ValueError("delivered_page_count must match non-blank page dispositions")
                if self.discarded_blank_page_count != sum(
                    page.delivery_disposition == "discarded_blank" for page in self.pages
                ):
                    raise ValueError("discarded_blank_page_count must match page dispositions")
                if self.review_page_count != sum(
                    page.status == AgenticPageStatus.REVIEW_REQUIRED for page in self.pages
                ):
                    raise ValueError("review_page_count must match page review statuses")
            if self.schema_version in {"1.2", "1.3", "1.4"}:
                if len(self.unverified_pages) != self.unverified_page_count:
                    raise ValueError("unverified_page_count must match unverified_pages")
                if len(self.unverified_pages) != len(set(self.unverified_pages)):
                    raise ValueError("unverified_pages must contain unique page numbers")
                if any(page_number < 1 or page_number > self.expected_page_count for page_number in self.unverified_pages):
                    raise ValueError("unverified_pages must be within the expected page range")
            if self.schema_version in {"1.3", "1.4"}:
                if any(value is None for value in (
                    self.content_selection_key,
                    self.content_selection_sha256,
                    self.content_selection_revision,
                    self.selected_page_count,
                    self.excluded_page_count,
                )):
                    raise ValueError("completed 1.3 runs must pin a complete content-selection revision")
                if self.selected_page_count + self.excluded_page_count != self.expected_page_count:
                    raise ValueError("content-selection counts must cover every physical page")
            if self.schema_version == "1.4" and any(value is None for value in (
                self.processed_book_key,
                self.processed_book_sha256,
                self.processed_book_revision,
                self.processed_page_count,
                self.contents_status,
            )):
                raise ValueError("completed 1.4 runs must pin the processed-book manifest")

        return self


class ExtractionPage(BaseModel):
    """One page of the completed extraction, as the extraction manifest lists it."""

    model_config = ConfigDict(extra = "forbid")

    page: StrictInt = Field(gt = 0)
    key: str = Field(min_length = 1)
    pictures: StrictInt = Field(default = 0, ge = 0)


class ExtractionRequest(BaseModel):
    """A finished extraction run and its pages, in physical order, each at its canonical raw object key."""

    model_config = ConfigDict(extra = "forbid")

    book_id: SafeSegment
    extraction_run_id: SafeSegment
    extraction_status: Literal["done"]
    expected_page_count: StrictInt = Field(gt = 0)
    page_refs: list[ExtractionPage]

    @model_validator(mode = "after")
    def _validate_pages(self) -> ExtractionRequest:
        if len(self.page_refs) != self.expected_page_count:
            raise ValueError("page_refs must cover the expected physical page count")
        for expected_page, page_ref in enumerate(self.page_refs, start = 1):
            if page_ref.page != expected_page:
                raise ValueError("page_refs must list every page once in physical page order")
            expected_key = (
                f"bd/books/{self.book_id}/ingestion/raw/{self.extraction_run_id}/pages/"
                f"{page_ref.page:04d}.md"
            )
            if page_ref.key != expected_key:
                raise ValueError(f"page {page_ref.page} does not use its canonical raw object key")
        return self

    def page_references(self) -> list[dict[str, int | str]]:
        """The pages as the plain dicts the graph state keeps."""
        return [page_ref.model_dump() for page_ref in self.page_refs]


__all__ = [
    "AgenticNodeStatus",
    "AgenticPageStatus",
    "AgenticRunStatus",
    "AgenticRunProgress",
    "ExtractionPage",
    "ExtractionRequest",
    "IngestionAIRunManifest",
    "IngestionPageArtifact",
    "ContentSelectionManifest",
    "ContentSelectionPage",
]
