"""Pydantic contracts exchanged by Book Distiller ingestion agents."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel, 
    ConfigDict, 
    Field, 
    StringConstraints, 
    field_validator, 
    model_validator
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
Confidence = Annotated[float, Field(ge = 0.0, le = 1.0, allow_inf_nan = False)]


class PageKind(StrEnum):
    TEXT = "text"
    BLANK = "blank"
    IMAGE_ONLY = "image_only"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class IssueCode(StrEnum):
    CONTROL_CHARACTERS = "control_characters"
    SPLIT_WORD = "split_word"
    SOFT_HYPHEN = "soft_hyphen"
    TOC_NOISE = "toc_noise"
    FUSED_MARKER = "fused_marker"
    TEXT_CORRUPTION = "text_corruption"
    STRUCTURE = "structure"
    TABLE_OR_INDEX_LAYOUT = "table_or_index_layout"
    METADATA = "metadata"
    OTHER = "other"


class IssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceSource(StrEnum):
    PAGE_MARKDOWN = "page_markdown"
    DOCLING_STRUCTURED = "docling_structured"
    BOOK_METADATA = "book_metadata"
    SOURCE_DOCUMENT = "source_document"


class PatchRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewDisposition(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    EDIT = "edit"
    ESCALATE = "escalate"


class SpecialistRole(StrEnum):
    METADATA = "metadata"
    TEXT_REPAIR = "text_repair"
    STRUCTURE = "structure"


_ALLOWED_ISSUES_BY_ROLE = {
    SpecialistRole.METADATA: frozenset({IssueCode.METADATA}),
    SpecialistRole.TEXT_REPAIR: frozenset({
        IssueCode.CONTROL_CHARACTERS,
        IssueCode.SPLIT_WORD,
        IssueCode.SOFT_HYPHEN,
        IssueCode.FUSED_MARKER,
        IssueCode.TEXT_CORRUPTION,
    }),
    SpecialistRole.STRUCTURE: frozenset({
        IssueCode.STRUCTURE,
        IssueCode.TOC_NOISE,
        IssueCode.TABLE_OR_INDEX_LAYOUT,
    }),
}


class EvidenceRef(BaseModel):
    """A source excerpt and optional locator supporting an agent finding."""

    model_config = ConfigDict(extra = "forbid")

    source: EvidenceSource
    excerpt: NonEmptyText
    page_number: int | None = Field(default = None, gt = 0)
    locator: NonEmptyText | None = None


class PageIssue(BaseModel):
    """One evidence-backed quality issue found on a page."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    code: IssueCode
    severity: IssueSeverity
    description: NonEmptyText
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(min_length = 1)


class PageAssessment(BaseModel):
    """Agent assessment for one physical page; pages are never omitted."""

    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0)
    page_kind: PageKind
    confidence: Confidence
    issues: list[PageIssue] = Field(default_factory = list)

    @model_validator(mode = "after")
    def _validate_unique_issue_ids(self) -> PageAssessment:
        issue_ids = [issue.issue_id for issue in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue_ids must be unique within a page assessment")
        return self


class WorkIssueRef(BaseModel):
    """A specialist assignment's exact page-local finding reference."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    page_number: int = Field(gt = 0)
    issue_code: IssueCode


class WorkItem(BaseModel):
    """One bounded, evidence-backed assignment to an allowlisted specialist."""

    model_config = ConfigDict(extra = "forbid")

    role: SpecialistRole
    issue_refs: list[WorkIssueRef] = Field(min_length = 1, max_length = 32)
    evidence: list[EvidenceRef] = Field(min_length = 1)
    rationale: NonEmptyText

    @model_validator(mode = "after")
    def _validate_role_issue_codes(self) -> WorkItem:
        allowed = _ALLOWED_ISSUES_BY_ROLE[self.role]
        issue_refs = [
            (issue_ref.page_number, issue_ref.issue_id)
            for issue_ref in self.issue_refs
        ]
        if len(issue_refs) != len(set(issue_refs)):
            raise ValueError("issue_refs must not contain duplicate page-local references")
        unsupported = {issue_ref.issue_code for issue_ref in self.issue_refs} - allowed
        if unsupported:
            values = ", ".join(sorted(issue.value for issue in unsupported))
            raise ValueError(f"{self.role.value} cannot handle issue codes: {values}")
        return self


class WorkPlan(BaseModel):
    """A bounded coordinator plan; an empty plan means no specialist work is needed."""

    model_config = ConfigDict(extra = "forbid")

    work_items: list[WorkItem] = Field(default_factory = list, max_length = 64)

    @model_validator(mode = "after")
    def _validate_unique_issue_assignments(self) -> WorkPlan:
        issue_refs = [
            (issue_ref.page_number, issue_ref.issue_id)
            for work_item in self.work_items
            for issue_ref in work_item.issue_refs
        ]
        if len(issue_refs) != len(set(issue_refs)):
            raise ValueError("a page-local issue reference can be assigned only once")
        return self


class PatchProposal(BaseModel):
    """A minimal, source-version-bound text patch proposed by an agent."""

    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0)
    issue_id: NonEmptyText
    source_sha256: str = Field(min_length = 64, max_length = 64)
    issue_code: IssueCode
    risk: PatchRisk
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText
    confidence: Confidence
    evidence: list[EvidenceRef] = Field(min_length = 1)

    @field_validator("source_sha256")
    @classmethod
    def _validate_source_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        return value

    @model_validator(mode = "after")
    def _reject_no_op(self) -> PatchProposal:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PatchProposalBatch(BaseModel):
    """Bounded specialist output for one physical page."""

    model_config = ConfigDict(extra = "forbid")

    proposals: list[PatchProposal] = Field(default_factory = list, max_length = 32)


class BookMetadataProposal(BaseModel):
    """Evidence-backed normalized book metadata proposed by the metadata agent."""

    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    subtitle: NonEmptyText | None = None
    authors: list[NonEmptyText] = Field(default_factory = list, max_length = 16)
    confidence: Confidence
    rationale: NonEmptyText
    evidence: list[EvidenceRef] = Field(min_length = 1, max_length = 16)


class ReviewDecision(BaseModel):
    """A human or independent reviewer decision tied to one proposal."""

    model_config = ConfigDict(extra = "forbid")

    proposal_id: NonEmptyText
    disposition: ReviewDisposition
    rationale: NonEmptyText
    edited_replacement: str | None = None

    @model_validator(mode = "after")
    def _validate_edit(self) -> ReviewDecision:
        if self.disposition == ReviewDisposition.EDIT and self.edited_replacement is None:
            raise ValueError("edited_replacement is required for an edit disposition")
        if self.disposition != ReviewDisposition.EDIT and self.edited_replacement is not None:
            raise ValueError("edited_replacement is only valid for an edit disposition")
        return self


__all__ = [
    "EvidenceRef",
    "EvidenceSource",
    "BookMetadataProposal",
    "IssueCode",
    "IssueSeverity",
    "PageAssessment",
    "PageIssue",
    "PageKind",
    "PatchProposalBatch",
    "PatchProposal",
    "PatchRisk",
    "ReviewDecision",
    "ReviewDisposition",
    "SpecialistRole",
    "WorkIssueRef",
    "WorkItem",
    "WorkPlan",
]
