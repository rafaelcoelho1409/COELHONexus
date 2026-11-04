"""Validated contracts for persisted Book Distiller AI ingestion runs."""
from __future__ import annotations
from . import versions

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


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

    schema_version: Literal["1.0"] = versions.MANIFEST_SCHEMA_VERSION
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

        return self


__all__ = [
    "AgenticNodeStatus",
    "AgenticPageStatus",
    "AgenticRunStatus",
    "AgenticRunProgress",
    "IngestionAIRunManifest",
    "IngestionPageArtifact",
]
