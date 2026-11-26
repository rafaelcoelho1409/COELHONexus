"""Pydantic contracts exchanged by Book Distiller ingestion agents."""
from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal

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
VisualChartAxis = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 120)] | float


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


class PatchVerificationVerdict(StrEnum):
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"


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
        IssueCode.OTHER,
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


class ContentsEntryProposal(BaseModel):
    """A contents heading mapped to one supplied physical page candidate."""

    model_config = ConfigDict(extra = "forbid")

    outline_index: int = Field(ge = 0, strict = True)
    title: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 300)]
    level: int = Field(ge = 1, le = 8, strict = True)
    source_page_number: int = Field(gt = 0, strict = True)
    confidence: Confidence
    rationale: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 240)]


class ContentsReconciliationProposal(BaseModel):
    """Bounded, evidence-grounded correction of a batch of table-of-contents entries."""

    model_config = ConfigDict(extra = "forbid")

    entries: list[ContentsEntryProposal] = Field(max_length = 100)


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


class PatchVerification(BaseModel):
    """A visual verifier's literal reading and line-wrap assessment for a correction."""

    model_config = ConfigDict(extra = "forbid")

    issue_id: NonEmptyText = Field(description = "The exact issue_id from the verification request.")
    verdict: PatchVerificationVerdict = Field(
        description = "Whether the literal source image supports the proposed replacement_focus.",
    )
    source_reading: str = Field(
        default = "",
        description = "Literal text read from the image at the minimal changed span, not the surrounding sentence.",
    )
    line_break_hyphen: bool = Field(
        default = False,
        description = "True only when a visible line-ending hyphen is a typographic wrap within one unhyphenated word.",
    )
    reason: NonEmptyText = Field(description = "A short explanation consistent with verdict and source_reading.")


class PatchVerificationBatch(BaseModel):
    """Visual verification decisions for the bounded proposals on one page."""

    model_config = ConfigDict(extra = "forbid")

    verifications: list[PatchVerification] = Field(default_factory = list, max_length = 32)


class VisualKind(StrEnum):
    DIAGRAM = "diagram"
    TABLE = "table"
    CHART = "chart"
    EQUATION = "equation"
    CODE = "code"
    TEXT = "text"
    TIMELINE = "timeline"
    MAP = "map"


class VisualDiagramNode(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    id: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 40)]
    label: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 240)]


class VisualDiagramEdge(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 40)]
    target: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 40)]
    label: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 160)] = ""


class VisualTimelineEvent(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    period: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 120)]
    label: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 240)]


class VisualChartDatum(BaseModel):
    """One explicitly legible chart row; application code maps it to a safe Vega-Lite spec."""

    model_config = ConfigDict(extra = "forbid")

    x: VisualChartAxis
    y: VisualChartAxis | None = None
    value: float | None = None
    series: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 120)] = ""

    @field_validator("x", "y", "value")
    @classmethod
    def _validate_finite_numbers(cls, value: str | float | None) -> str | float | None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("chart values must be finite")
        return value


class VisualMapCoordinate(BaseModel):
    """A geographic coordinate transcribed from an explicit source label or coordinate list."""

    model_config = ConfigDict(extra = "forbid")

    longitude: float = Field(ge = -180, le = 180, allow_inf_nan = False)
    latitude: float = Field(ge = -90, le = 90, allow_inf_nan = False)


class VisualMapFeature(BaseModel):
    """A bounded GeoJSON geometry whose coordinates must be explicit in the source."""

    model_config = ConfigDict(extra = "forbid")

    label: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 160)] = ""
    category: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 100)] = ""
    geometry_type: Literal["Point", "LineString", "Polygon"]
    coordinates: list[VisualMapCoordinate] = Field(min_length = 1, max_length = 500)

    @model_validator(mode = "after")
    def _validate_geometry(self) -> VisualMapFeature:
        count = len(self.coordinates)
        if self.geometry_type == "Point" and count != 1:
            raise ValueError("a point feature requires exactly one coordinate")
        if self.geometry_type == "LineString" and count < 2:
            raise ValueError("a line feature requires at least two coordinates")
        if (
            self.geometry_type == "Polygon"
            and (count < 4 or self.coordinates[0] != self.coordinates[-1])
        ):
            raise ValueError("a polygon ring requires four coordinates and must be closed")
        return self


class VisualExtractionResult(BaseModel):
    """Constrained transcription of one source figure; renderable Markdown is built by trusted code."""

    model_config = ConfigDict(extra = "forbid")

    decision: Literal["convert", "retain"]
    kind: VisualKind | None = None
    confidence: Confidence
    rationale: Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 500)]
    diagram_direction: Literal["TD", "LR", "BT", "RL"] = "TD"
    diagram_nodes: list[VisualDiagramNode] = Field(default_factory = list, max_length = 60)
    diagram_edges: list[VisualDiagramEdge] = Field(default_factory = list, max_length = 120)
    table_headers: list[Annotated[str, StringConstraints(max_length = 300)]] = Field(default_factory = list, max_length = 30)
    table_rows: list[list[Annotated[str, StringConstraints(max_length = 500)]]] = Field(
        default_factory = list,
        max_length = 120,
    )
    chart_title: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 200)] = ""
    chart_type: Literal["bar", "line", "area", "scatter", "pie", "heatmap"] = "bar"
    chart_x_label: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 120)] = ""
    chart_y_label: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 120)] = ""
    chart_categories: list[Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1, max_length = 100)]] = Field(
        default_factory = list,
        max_length = 40,
    )
    chart_values: list[float] = Field(default_factory = list, max_length = 40)
    chart_rows: list[VisualChartDatum] = Field(default_factory = list, max_length = 120)
    map_title: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 200)] = ""
    map_features: list[VisualMapFeature] = Field(default_factory = list, max_length = 40)
    equation_latex: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 3000)] = ""
    code_language: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 32)] = ""
    code_text: Annotated[str, StringConstraints(max_length = 8000)] = ""
    text_title: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 160)] = ""
    transcribed_text: Annotated[str, StringConstraints(max_length = 12000)] = ""
    text_format: Literal["prose", "code"] = "prose"
    timeline_title: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 200)] = ""
    timeline_events: list[VisualTimelineEvent] = Field(default_factory = list, max_length = 60)

    @model_validator(mode = "after")
    def _validate_conversion_payload(self) -> VisualExtractionResult:
        if self.decision == "retain":
            return self
        if self.kind is None:
            raise ValueError("a conversion must identify its visual kind")
        if self.kind == VisualKind.DIAGRAM:
            identifiers = [node.id for node in self.diagram_nodes]
            if len(identifiers) < 2 or len(identifiers) != len(set(identifiers)):
                raise ValueError("a diagram must have at least two nodes with unique identifiers")
            if not self.diagram_edges or any(
                edge.source not in identifiers or edge.target not in identifiers
                for edge in self.diagram_edges
            ):
                raise ValueError("diagram edges must connect supplied nodes")
        elif self.kind == VisualKind.TABLE:
            if not self.table_headers or not self.table_rows:
                raise ValueError("a table conversion must include headers and rows")
            if len(self.table_headers) > 20 or any(len(row) != len(self.table_headers) for row in self.table_rows):
                raise ValueError("table rows must match the supplied header count")
        elif self.kind == VisualKind.CHART:
            if self.chart_rows:
                if self.chart_type == "scatter":
                    valid = all(
                        isinstance(row.x, (int, float))
                        and not isinstance(row.x, bool)
                        and isinstance(row.y, (int, float))
                        and not isinstance(row.y, bool)
                        and row.value is None
                        for row in self.chart_rows
                    )
                elif self.chart_type == "heatmap":
                    valid = all(
                        isinstance(row.x, str)
                        and row.x.strip()
                        and isinstance(row.y, str)
                        and row.y.strip()
                        and row.value is not None
                        and not row.series
                        for row in self.chart_rows
                    )
                else:
                    valid = all(
                        isinstance(row.x, (str, int, float))
                        and (not isinstance(row.x, str) or row.x.strip())
                        and row.y is None
                        and row.value is not None
                        and (self.chart_type != "pie" or not row.series)
                        for row in self.chart_rows
                    )
                if not valid or any(
                    row.value is not None and not math.isfinite(row.value)
                    for row in self.chart_rows
                ):
                    raise ValueError("chart rows do not match the selected chart type")
            elif (
                self.chart_type not in {"bar", "line", "area", "pie"}
                or not self.chart_categories
                or len(self.chart_categories) != len(self.chart_values)
                or any(not math.isfinite(value) for value in self.chart_values)
            ):
                raise ValueError("a chart requires finite values in a supported data shape")
        elif self.kind == VisualKind.EQUATION and not self.equation_latex:
            raise ValueError("an equation conversion must include LaTeX")
        elif self.kind == VisualKind.CODE and not self.code_text.strip():
            raise ValueError("a code conversion must include visible source text")
        elif self.kind == VisualKind.TEXT and not self.transcribed_text.strip():
            raise ValueError("a text conversion must include visible source text")
        elif self.kind == VisualKind.TIMELINE and len(self.timeline_events) < 2:
            raise ValueError("a timeline conversion must include at least two visible events")
        elif self.kind == VisualKind.MAP:
            coordinate_count = sum(len(feature.coordinates) for feature in self.map_features)
            if not self.map_features or coordinate_count > 500:
                raise ValueError("a map conversion requires bounded, explicit geographic features")
        return self


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
    "MECHANICAL_ISSUES",
    "AuditIssueCode",
    "BookMetadataProposal",
    "ContentPageAssessment",
    "ContentPageRole",
    "EvidenceRef",
    "EvidenceSource",
    "FindingCheck",
    "FindingCheckBatch",
    "FindingVerdict",
    "IssueCode",
    "IssueSeverity",
    "PageAssessment",
    "PageAuditFinding",
    "PageAuditResponse",
    "PageIssue",
    "PageKind",
    "PatchDraft",
    "PatchDraftBatch",
    "PatchProposal",
    "PatchProposalBatch",
    "PatchRisk",
    "PatchVerification",
    "PatchVerificationBatch",
    "PatchVerificationVerdict",
    "SpecialistRole",
    "WorkIssueRef",
    "WorkItem",
]
