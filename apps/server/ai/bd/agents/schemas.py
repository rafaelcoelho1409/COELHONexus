"""Pydantic contracts exchanged by Book Distiller ingestion agents."""
from __future__ import annotations

import enum
import typing

import pydantic


NonEmptyText = typing.Annotated[str, pydantic.StringConstraints(strip_whitespace = True, min_length = 1)]
Confidence = typing.Annotated[float, pydantic.Field(ge = 0.0, le = 1.0, allow_inf_nan = False)]


class PageKind(enum.StrEnum):
    TEXT = "text"
    BLANK = "blank"
    IMAGE_ONLY = "image_only"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ContentPageRole(enum.StrEnum):
    """A page's role in the book, separate from extraction-quality findings."""

    CORE = "core"
    PREFACE = "preface"
    REFERENCE = "reference"
    NAVIGATION = "navigation"
    LEGAL = "legal"
    NOISE = "noise"
    UNKNOWN = "unknown"


class IssueCode(enum.StrEnum):
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


class AuditIssueCode(enum.StrEnum):
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


class FindingVerdict(enum.StrEnum):
    DEFECT = "defect"
    NOT_A_DEFECT = "not_a_defect"


class IssueSeverity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceSource(enum.StrEnum):
    PAGE_MARKDOWN = "page_markdown"
    BOOK_METADATA = "book_metadata"


class PatchRisk(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SpecialistRole(enum.StrEnum):
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


class EvidenceRef(pydantic.BaseModel):
    """A source excerpt and optional locator supporting an agent finding."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    source: EvidenceSource
    excerpt: NonEmptyText
    page_number: int | None = pydantic.Field(default = None, gt = 0)
    locator: NonEmptyText | None = None


class PageIssue(pydantic.BaseModel):
    """One evidence-backed quality issue found on a page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    code: IssueCode
    severity: IssueSeverity
    description: NonEmptyText
    confidence: Confidence
    evidence: list[EvidenceRef] = pydantic.Field(min_length = 1)


class PageAssessment(pydantic.BaseModel):
    """Agent assessment for one physical page; pages are never omitted."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    page_number: int = pydantic.Field(gt = 0)
    page_kind: PageKind
    confidence: Confidence
    issues: list[PageIssue] = pydantic.Field(default_factory = list)

    @pydantic.model_validator(mode = "after")
    def _validate_unique_issue_ids(self) -> PageAssessment:
        issue_ids = [issue.issue_id for issue in self.issues]
        if len(issue_ids) != len(set(issue_ids)):
            raise ValueError("issue_ids must be unique within a page assessment")
        return self


class ContentPageAssessment(pydantic.BaseModel):
    """Content-role classification for one normalized physical page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    role: ContentPageRole
    confidence: Confidence
    rationale: typing.Annotated[str, pydantic.StringConstraints(strip_whitespace = True, min_length = 1, max_length = 600)]


class PageAuditFinding(pydantic.BaseModel):
    """Minimal model-facing finding; page locators and evidence objects are added by the application."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    code: AuditIssueCode
    severity: IssueSeverity
    description: NonEmptyText
    confidence: Confidence
    evidence_excerpts: list[NonEmptyText] = pydantic.Field(min_length = 1, max_length = 8)
    # Decided last, after the evidence is written: a finding that on reflection is normal Docling output is marked so.
    verdict: FindingVerdict


class FindingCheck(pydantic.BaseModel):
    """A second reader's decision on one finding."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    verdict: FindingVerdict
    reason: NonEmptyText


class FindingCheckBatch(pydantic.BaseModel):
    """The verifier's decisions, one per finding it was shown."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    checks: list[FindingCheck] = pydantic.Field(default_factory = list, max_length = 32)


class PageAuditResponse(pydantic.BaseModel):
    """Minimal model-facing response for one physical page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    page_kind: PageKind
    confidence: Confidence
    findings: list[PageAuditFinding] = pydantic.Field(default_factory = list, max_length = 32)


class WorkIssueRef(pydantic.BaseModel):
    """A specialist assignment's exact page-local finding reference."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    page_number: int = pydantic.Field(gt = 0)
    issue_code: IssueCode


class WorkItem(pydantic.BaseModel):
    """One bounded, evidence-backed assignment to an allowlisted specialist."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    role: SpecialistRole
    issue_refs: list[WorkIssueRef] = pydantic.Field(min_length = 1, max_length = 32)
    evidence: list[EvidenceRef] = pydantic.Field(min_length = 1)
    rationale: NonEmptyText

    @pydantic.model_validator(mode = "after")
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


class PatchProposal(pydantic.BaseModel):
    """A minimal, source-version-bound text patch proposed by an agent."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    page_number: int = pydantic.Field(gt = 0)
    issue_id: NonEmptyText
    source_sha256: str = pydantic.Field(min_length = 64, max_length = 64)
    issue_code: IssueCode
    risk: PatchRisk
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText
    confidence: Confidence
    evidence: list[EvidenceRef] = pydantic.Field(min_length = 1)

    @pydantic.field_validator("source_sha256")
    @classmethod
    def _validate_source_sha256(cls, value: str) -> str:
        if any(character not in "0123456789abcdef" for character in value):
            raise ValueError("source_sha256 must be lowercase hexadecimal")
        return value

    @pydantic.model_validator(mode = "after")
    def _reject_no_op(self) -> PatchProposal:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PatchDraft(pydantic.BaseModel):
    """Model-facing patch fields; page identity, issue code, and evidence are filled from trusted input."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    issue_id: NonEmptyText
    risk: PatchRisk
    context_before: str = ""
    target_text: NonEmptyText
    context_after: str = ""
    replacement: str
    rationale: NonEmptyText
    confidence: Confidence

    @pydantic.model_validator(mode = "after")
    def _reject_no_op(self) -> PatchDraft:
        if self.target_text == self.replacement:
            raise ValueError("replacement must change target_text")
        return self


class PatchDraftBatch(pydantic.BaseModel):
    """Bounded minimal specialist output for one physical page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    proposals: list[PatchDraft] = pydantic.Field(default_factory = list, max_length = 32)


class PatchProposalBatch(pydantic.BaseModel):
    """Bounded specialist output for one physical page."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    proposals: list[PatchProposal] = pydantic.Field(default_factory = list, max_length = 32)


class BookMetadataProposal(pydantic.BaseModel):
    """Evidence-backed normalized book metadata proposed by the metadata agent."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    title: NonEmptyText
    subtitle: NonEmptyText | None = None
    authors: list[NonEmptyText] = pydantic.Field(default_factory = list, max_length = 16)
    confidence: Confidence
    rationale: NonEmptyText
    evidence: list[EvidenceRef] = pydantic.Field(min_length = 1, max_length = 16)


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
