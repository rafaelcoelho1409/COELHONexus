"""Pydantic contracts exchanged by Book Distiller ingestion agents."""
from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    StringConstraints,
)


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
Confidence = Annotated[float, Field(ge = 0.0, le = 1.0, allow_inf_nan = False)]


class PageKind(StrEnum):
    TEXT = "text"
    BLANK = "blank"
    IMAGE_ONLY = "image_only"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ContentPageRole(StrEnum):
    """A page's role in the book, separate from extraction-quality findings."""

    CORE = "core"
    PREFACE = "preface"
    REFERENCE = "reference"
    NAVIGATION = "navigation"
    LEGAL = "legal"
    NOISE = "noise"
    UNKNOWN = "unknown"


class IssueCode(StrEnum):
    CONTROL_CHARACTERS = "control_characters"
    NEAR_EMPTY = "near_empty"
    SPLIT_WORD = "split_word"
    SOFT_HYPHEN = "soft_hyphen"
    TOC_NOISE = "toc_noise"
    FUSED_MARKER = "fused_marker"
    TEXT_CORRUPTION = "text_corruption"
    STRUCTURE = "structure"
    TABLE_OR_INDEX_LAYOUT = "table_or_index_layout"
    METADATA = "metadata"
    OTHER = "other"


class AuditIssueCode(StrEnum):
    """What the page auditor may report: sparseness is measured in code, and book metadata has its own stage."""

    CONTROL_CHARACTERS = "control_characters"
    SPLIT_WORD = "split_word"
    SOFT_HYPHEN = "soft_hyphen"
    TOC_NOISE = "toc_noise"
    FUSED_MARKER = "fused_marker"
    TEXT_CORRUPTION = "text_corruption"
    STRUCTURE = "structure"
    TABLE_OR_INDEX_LAYOUT = "table_or_index_layout"
    OTHER = "other"


class FindingVerdict(StrEnum):
    DEFECT = "defect"
    NOT_A_DEFECT = "not_a_defect"


class IssueSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceSource(StrEnum):
    PAGE_MARKDOWN = "page_markdown"
    BOOK_METADATA = "book_metadata"


class PatchRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SpecialistRole(StrEnum):
    TEXT_REPAIR = "text_repair"
    STRUCTURE = "structure"


# Findings that code fixes by itself, so no specialist is asked about them.
MECHANICAL_ISSUES = frozenset({IssueCode.CONTROL_CHARACTERS, IssueCode.SOFT_HYPHEN})

# The issue codes each specialist may be assigned and may propose patches for.
ALLOWED_ISSUES_BY_ROLE = {
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


class ContentPageAssessment(BaseModel):
    """Content-role classification for one normalized physical page."""

    model_config = ConfigDict(extra = "forbid")

    role: ContentPageRole
    confidence: Confidence
    rationale: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 600)]


class PageAuditFinding(BaseModel):
    """Minimal model-facing finding; page locators and evidence objects are added by the application."""

    model_config = ConfigDict(extra = "forbid")

    code: AuditIssueCode
    severity: IssueSeverity
    description: NonEmptyText
    confidence: Confidence
    evidence_excerpts: list[NonEmptyText] = Field(min_length = 1, max_length = 8)
    # Decided last, after the evidence is written: a finding that on reflection is normal Docling output is marked so.
    verdict: FindingVerdict


class FindingCheck(BaseModel):
    """A second reader's decision on one finding."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    verdict: FindingVerdict
    reason: NonEmptyText


class FindingCheckBatch(BaseModel):
    """The verifier's decisions, one per finding it was shown."""

    model_config = ConfigDict(extra = "forbid")

    checks: list[FindingCheck] = Field(default_factory = list, max_length = 32)


class PageAuditResponse(BaseModel):
    """Minimal model-facing response for one physical page."""

    model_config = ConfigDict(extra = "forbid")

    page_kind: PageKind
    confidence: Confidence
    findings: list[PageAuditFinding] = Field(default_factory = list, max_length = 32)


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
        allowed = ALLOWED_ISSUES_BY_ROLE[self.role]
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


class PatchDraft(BaseModel):
    """Model-facing patch fields; page identity, issue code, and evidence are filled from trusted input."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    risk: PatchRisk
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText
    confidence: Confidence

    @model_validator(mode = "after")
    def _reject_no_op(self) -> PatchDraft:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PatchDraftBatch(BaseModel):
    """Bounded minimal specialist output for one physical page."""

    model_config = ConfigDict(extra = "forbid")

    proposals: list[PatchDraft] = Field(default_factory = list, max_length = 32)


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


__all__ = [
    "ALLOWED_ISSUES_BY_ROLE",
    "AuditIssueCode",
    "EvidenceRef",
    "EvidenceSource",
    "BookMetadataProposal",
    "ContentPageAssessment",
    "ContentPageRole",
    "FindingCheck",
    "FindingCheckBatch",
    "FindingVerdict",
    "IssueCode",
    "IssueSeverity",
    "MECHANICAL_ISSUES",
    "PageAssessment",
    "PageAuditFinding",
    "PageAuditResponse",
    "PageIssue",
    "PageKind",
    "PatchProposalBatch",
    "PatchDraftBatch",
    "PatchDraft",
    "PatchProposal",
    "PatchRisk",
    "SpecialistRole",
    "WorkIssueRef",
    "WorkItem",
]
