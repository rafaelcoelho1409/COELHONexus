"""Pydantic contracts exchanged by Docs Distiller ingestion agents."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
Confidence = Annotated[float, Field(ge = 0.0, le = 1.0, allow_inf_nan = False)]


class PageKind(StrEnum):
    DOCUMENTATION = "documentation"
    INDEX = "index"
    API_REFERENCE = "api_reference"
    CHANGELOG = "changelog"
    TUTORIAL = "tutorial"
    OTHER = "other"


class IssueCategory(StrEnum):
    DUPLICATE_BOILERPLATE = "duplicate_boilerplate"
    DUPLICATE_SECTION = "duplicate_section"
    MALFORMED_HEADING = "malformed_heading"
    MARKDOWN_FORMATTING = "markdown_formatting"
    GENERATED_TITLE = "generated_title"
    ENCODING_ARTIFACT = "encoding_artifact"
    OTHER = "other"


class IssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PageIssue(BaseModel):
    """One page-quality finding with source text that can be checked exactly."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    category: IssueCategory
    severity: IssueSeverity
    description: NonEmptyText
    evidence: NonEmptyText
    confidence: Confidence


class PageAssessment(BaseModel):
    """Structured audit for one extracted documentation page."""

    model_config = ConfigDict(extra = "forbid")

    page_kind: PageKind
    confidence: Confidence
    title: str = ""
    title_confidence: Confidence = 0.0
    summary: str = ""
    issues: list[PageIssue] = Field(default_factory = list, max_length = 24)

    @field_validator("title", "summary")
    @classmethod
    def _strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode = "after")
    def _validate_unique_issue_ids(self) -> PageAssessment:
        issue_ids = [issue.issue_id for issue in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue_ids must be unique within a page assessment")
        return self


class PagePatch(BaseModel):
    """A minimal patch tied to an exact source version and audited finding."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    source_sha256: str = Field(min_length = 64, max_length = 64)
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText
    evidence: NonEmptyText
    confidence: Confidence

    @field_validator("source_sha256")
    @classmethod
    def _validate_source_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        return value

    @model_validator(mode = "after")
    def _reject_no_op(self) -> PagePatch:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PagePatchBatch(BaseModel):
    """Bounded patch output for a single page."""

    model_config = ConfigDict(extra = "forbid")

    patches: list[PagePatch] = Field(default_factory = list, max_length = 24)
