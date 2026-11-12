"""Pydantic contracts exchanged by Docs Distiller ingestion agents."""
from __future__ import annotations

import enum
import typing

import pydantic


NonEmptyText = typing.Annotated[str, pydantic.StringConstraints(strip_whitespace = True, min_length = 1)]
Confidence = typing.Annotated[float, pydantic.Field(ge = 0.0, le = 1.0, allow_inf_nan = False)]


class PageKind(enum.StrEnum):
    DOCUMENTATION = "documentation"
    INDEX = "index"
    API_REFERENCE = "api_reference"
    CHANGELOG = "changelog"
    TUTORIAL = "tutorial"
    OTHER = "other"


class IssueCategory(enum.StrEnum):
    DUPLICATE_BOILERPLATE = "duplicate_boilerplate"
    DUPLICATE_SECTION = "duplicate_section"
    MALFORMED_HEADING = "malformed_heading"
    MARKDOWN_FORMATTING = "markdown_formatting"
    GENERATED_TITLE = "generated_title"
    ENCODING_ARTIFACT = "encoding_artifact"
    OTHER = "other"


class IssueSeverity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PageIssue(pydantic.BaseModel):
    """One page-quality finding with source text that can be checked exactly."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    category: IssueCategory
    severity: IssueSeverity
    description: NonEmptyText
    evidence: NonEmptyText
    other_evidence: str = pydantic.Field(
        default = "",
        description = "For a duplicate: the second copy of the evidence, copied exactly from a different place on the page. Empty otherwise.",
    )
    confidence: Confidence


class PageAssessment(pydantic.BaseModel):
    """Structured audit for one extracted documentation page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    page_kind: PageKind
    confidence: Confidence
    title: str = pydantic.Field(default = "", description = "The page's title as written in the text, without a leading `#` marker.")
    title_confidence: Confidence = 0.0
    summary: str = ""
    issues: list[PageIssue] = pydantic.Field(default_factory = list, max_length = 24)

    @pydantic.field_validator("title", "summary")
    @classmethod
    def _strip_optional_text(cls, value: str) -> str:
        return value.strip()

    @pydantic.model_validator(mode = "after")
    def _validate_unique_issue_ids(self) -> PageAssessment:
        issue_ids = [issue.issue_id for issue in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue_ids must be unique within a page assessment")
        return self


class PagePatch(pydantic.BaseModel):
    """A minimal patch tied to an exact source version and audited finding."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    source_sha256: str = pydantic.Field(min_length = 64, max_length = 64)
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText = pydantic.Field(description = "Why this edit resolves the finding; commentary belongs here.")
    evidence: NonEmptyText = pydantic.Field(
        description = "The audited finding's evidence, copied exactly; never an explanation or a paraphrase."
    )
    confidence: Confidence

    @pydantic.field_validator("source_sha256")
    @classmethod
    def _validate_source_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        return value

    @pydantic.model_validator(mode = "after")
    def _reject_no_op(self) -> PagePatch:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PagePatchBatch(pydantic.BaseModel):
    """Bounded patch output for a single page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    patches: list[PagePatch] = pydantic.Field(default_factory = list, max_length = 24)
