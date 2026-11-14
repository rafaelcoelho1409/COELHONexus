"""Validated agent, manifest, source, and Learning Guide contracts."""
from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from . import versions

SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]

_JSON_ARRAY_FIELDS = frozenset({
    "assumptions",
    "charts",
    "columns",
    "diagrams",
    "edges",
    "equations",
    "evidence",
    "lessons",
    "learning_unit_ids",
    "nodes",
    "omitted_material",
    "open_questions",
    "overview_learning_unit_ids",
    "rows",
    "transformations",
    "y_fields",
})
_JSON_OBJECT_ARRAY_FIELDS = frozenset({
    "charts",
    "columns",
    "diagrams",
    "edges",
    "equations",
    "evidence",
    "lessons",
    "nodes",
    "open_questions",
    "rows",
})
_JSON_OBJECT_FIELDS = frozenset({"dataset", "values"})
_JSON_COLLECTION_FIELDS = _JSON_ARRAY_FIELDS | _JSON_OBJECT_FIELDS


def _normalize_stringified_collections(value: Any, field_name: str | None = None) -> Any:
    """Unwrap JSON-encoded collection fields some structured-output providers double encode."""
    if isinstance(value, list):
        items = []
        for item in value:
            if field_name in _JSON_OBJECT_ARRAY_FIELDS and isinstance(item, str):
                try:
                    decoded = json.loads(item)
                except ValueError:
                    decoded = item
                if isinstance(decoded, dict):
                    item = decoded
            items.append(_normalize_stringified_collections(item))
        return items
    if not isinstance(value, dict):
        return value

    normalized = {}
    for key, item in value.items():
        if isinstance(item, str) and key in _JSON_COLLECTION_FIELDS:
            try:
                decoded = json.loads(item)
            except ValueError:
                decoded = item
            expected_type = list if key in _JSON_ARRAY_FIELDS else dict
            if isinstance(decoded, expected_type):
                item = decoded
        normalized[key] = _normalize_stringified_collections(item, key)
    return normalized


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0, strict = True)
    quote: NonEmptyText = Field(max_length = 800)


class ExtractedLesson(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    kind: Literal["principle", "definition", "method", "example", "fact", "caveat", "action", "question"]
    title: NonEmptyText = Field(max_length = 180)
    claim: NonEmptyText = Field(max_length = 1200)
    explanation: NonEmptyText = Field(max_length = 3000)
    attribution: Literal["book", "agent_inference"] = "book"
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 4)
    confidence: float = Field(ge = 0, le = 1)

    @model_validator(mode = "after")
    def _book_claim_has_evidence(self) -> ExtractedLesson:
        if self.attribution == "book" and not self.evidence:
            raise ValueError("book-attributed lessons require source evidence")
        return self


class LearningExtraction(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    lessons: list[ExtractedLesson] = Field(max_length = 8)
    omitted_material: list[NonEmptyText] = Field(max_length = 8)

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_collections(cls, data):
        """Normalize JSON-stringified lessons/evidence while retaining strict field validation."""
        return _normalize_stringified_collections(data)


class EvidenceVerdict(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    lesson_index: int = Field(ge = 0, strict = True)
    supported: bool
    reason: NonEmptyText = Field(max_length = 600)


class EvidenceReview(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    verdicts: list[EvidenceVerdict] = Field(max_length = 8)


class ChapterLesson(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 180)
    explanation: NonEmptyText = Field(max_length = 3000)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 12)


class ChapterSynthesis(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    overview: NonEmptyText = Field(max_length = 4000)
    overview_attribution: Literal["book", "agent_inference"] = "book"
    overview_learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 24)
    lessons: list[ChapterLesson] = Field(max_length = 24)
    open_questions: list[ChapterLesson] = Field(max_length = 8)
    diagrams: list[DiagramSpec] = Field(default_factory = list, max_length = 8)
    equations: list[EquationBlock] = Field(default_factory = list, max_length = 8)
    charts: list[ChartSpec] = Field(default_factory = list, max_length = 8)

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_collections(cls, data):
        """Normalize JSON-stringified nested collections before validating every field."""
        return _normalize_stringified_collections(data)


class BookSynthesisOutputLesson(BaseModel):
    """Compact model output for whole-book orientation, separate from persisted schema limits."""
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 100)
    explanation: NonEmptyText = Field(max_length = 320)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)


class BookSynthesisOutput(BaseModel):
    """Bounded LLM response shape for the concise whole-book summary."""
    model_config = ConfigDict(extra = "forbid")

    central_thesis: NonEmptyText = Field(max_length = 500)
    central_thesis_attribution: Literal["book", "agent_inference"] = "book"
    central_thesis_learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)
    key_takeaways: list[BookSynthesisOutputLesson] = Field(max_length = 4)
    cross_chapter_connections: list[BookSynthesisOutputLesson] = Field(max_length = 3)
    limitations: list[BookSynthesisOutputLesson] = Field(max_length = 2)

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_collections(cls, data):
        """Normalize JSON-stringified citation arrays while keeping output bounded."""
        return _normalize_stringified_collections(data)


class BookSynthesis(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    central_thesis: NonEmptyText = Field(max_length = 2500)
    central_thesis_attribution: Literal["book", "agent_inference"] = "book"
    central_thesis_learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 24)
    key_takeaways: list[ChapterLesson] = Field(max_length = 16)
    cross_chapter_connections: list[ChapterLesson] = Field(max_length = 12)
    limitations: list[ChapterLesson] = Field(max_length = 12)

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_collections(cls, data):
        """Normalize JSON-stringified citation arrays while keeping all schema limits."""
        return _normalize_stringified_collections(data)


class SourceRef(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_type: Literal["book", "agent_inference"]
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    ingestion_ai_run_id: SafeSegment
    chunk_id: SafeSegment
    source_key: NonEmptyText
    page_number: int = Field(gt = 0, strict = True)
    processed_page_number: int | None = Field(default = None, gt = 0, strict = True)
    section_path: list[str]
    source_sha256: Sha256Hex
    quote: NonEmptyText = Field(max_length = 800)


class ReportLesson(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    explanation: NonEmptyText
    source_refs: list[SourceRef] = Field(min_length = 1)
    attribution: Literal["book", "agent_inference"] = "book"
    verification_status: Literal["verified", "unverified"] = "unverified"
    confidence: float = Field(ge = 0, le = 1, default = 0.0)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    overview: NonEmptyText
    overview_attribution: Literal["book", "agent_inference"] = "book"
    overview_source_refs: list[SourceRef] = Field(min_length = 1)
    lessons: list[ReportLesson]
    open_questions: list[ReportLesson] = Field(default_factory = list)
    diagrams: list[DiagramSpec] = Field(default_factory = list)
    equations: list[EquationBlock] = Field(default_factory = list)
    charts: list[ChartSpec] = Field(default_factory = list)


class GlossaryEntry(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    term: NonEmptyText
    definition: NonEmptyText
    attribution: Literal["book", "agent_inference"] = "book"
    source_refs: list[SourceRef] = Field(min_length = 1)
    verification_status: Literal["verified", "unverified"]


class DiagramNode(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    node_id: SafeSegment
    label: NonEmptyText
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class DiagramEdge(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_id: SafeSegment
    target_id: SafeSegment
    relation: Literal["supports", "requires", "defines", "contrasts", "sequence", "example_of"]
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class DiagramSpec(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    accessible_description: NonEmptyText
    nodes: list[DiagramNode] = Field(min_length = 2, max_length = 24)
    edges: list[DiagramEdge] = Field(min_length = 1, max_length = 48)

    @model_validator(mode = "after")
    def _validate_graph(self) -> DiagramSpec:
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("diagram node IDs must be unique")
        known_ids = set(node_ids)
        if any(edge.source_id not in known_ids or edge.target_id not in known_ids for edge in self.edges):
            raise ValueError("diagram edges must connect declared nodes")
        return self


class EquationBlock(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    latex: NonEmptyText = Field(max_length = 2000)
    explanation: NonEmptyText
    attribution: Literal["book", "agent_inference"] = "book"
    assumptions: list[str] = Field(default_factory = list)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class DataColumn(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    name: NonEmptyText
    value_type: Literal["number", "text"]
    unit: str | None = None


class DatasetRow(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    values: dict[str, str | int | float | None]
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class Dataset(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    dataset_id: SafeSegment
    title: NonEmptyText
    columns: list[DataColumn] = Field(min_length = 2, max_length = 12)
    rows: list[DatasetRow] = Field(min_length = 1, max_length = 100)
    transformations: list[str] = Field(default_factory = list)
    assumptions: list[str] = Field(default_factory = list)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)

    @model_validator(mode = "after")
    def _validate_rows_match_columns(self) -> Dataset:
        names = [column.name for column in self.columns]
        if len(names) != len(set(names)):
            raise ValueError("dataset columns must have unique names")
        expected = set(names)
        for row in self.rows:
            if set(row.values) != expected:
                raise ValueError("every dataset row must provide exactly the declared columns")
            for column in self.columns:
                value = row.values[column.name]
                if value is None:
                    continue
                if column.value_type == "number":
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("numeric dataset columns must contain only numbers")
                    if isinstance(value, float) and not math.isfinite(value):
                        raise ValueError("dataset numbers must be finite")
                elif not isinstance(value, str):
                    raise ValueError("text dataset columns must contain only strings")
        return self


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    chart_type: Literal["bar", "line", "scatter", "table"]
    x_field: NonEmptyText
    y_fields: list[NonEmptyText] = Field(min_length = 1, max_length = 4)
    caption: NonEmptyText
    accessible_description: NonEmptyText
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1)
    dataset: Dataset

    @model_validator(mode = "after")
    def _validate_chart_fields(self) -> ChartSpec:
        available = {column.name for column in self.dataset.columns}
        if self.x_field not in available or set(self.y_fields) - available:
            raise ValueError("chart encodings must reference declared dataset columns")
        numeric_columns = {
            column.name for column in self.dataset.columns if column.value_type == "number"
        }
        if self.chart_type != "table" and set(self.y_fields) - numeric_columns:
            raise ValueError("chart value encodings must use numeric dataset columns")
        return self


class StudyGuideDocument(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1", "1.2"] = versions.REPORT_SCHEMA_VERSION
    title: NonEmptyText
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    ingestion_ai_run_id: SafeSegment
    distill_run_id: SafeSegment
    source_page_count: int = Field(default = 0, ge = 0, strict = True)
    selected_page_count: int = Field(default = 0, ge = 0, strict = True)
    excluded_page_count: int = Field(default = 0, ge = 0, strict = True)
    content_selection_revision: int = Field(default = 1, ge = 1, strict = True)
    processed_page_count: int = Field(default = 0, ge = 0, strict = True)
    processed_book_revision: int = Field(default = 1, ge = 1, strict = True)
    generated_at: float = Field(gt = 0)
    report_scope: Literal["book_only"] = "book_only"
    central_thesis: NonEmptyText
    central_thesis_attribution: Literal["book", "agent_inference"] = "book"
    central_thesis_source_refs: list[SourceRef] = Field(min_length = 1)
    key_takeaways: list[ReportLesson]
    cross_chapter_connections: list[ReportLesson]
    chapters: list[ReportSection]
    glossary: list[GlossaryEntry] = Field(default_factory = list)
    limitation_notes: list[ReportLesson] = Field(default_factory = list)
    review_notes: list[str] = Field(default_factory = list)
    diagrams: list[DiagramSpec] = Field(default_factory = list)
    equations: list[EquationBlock] = Field(default_factory = list)
    charts: list[ChartSpec] = Field(default_factory = list)
    covered_pages: list[int]
    uncovered_pages: list[int]
    review_required: bool


class DistillProgress(BaseModel):
    current_node: str | None = None
    node_status: dict[str, str] = Field(default_factory = dict)
    completed_units: int = 0
    total_units: int = 0
    completed_chapters: int = 0
    total_chapters: int = 0


class StructuralQuality(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    expected_page_count: int = Field(gt = 0, strict = True)
    selected_page_count: int = Field(default = 0, ge = 0, strict = True)
    excluded_page_count: int = Field(default = 0, ge = 0, strict = True)
    substantive_page_count: int = Field(ge = 0, strict = True)
    covered_substantive_pages: int = Field(ge = 0, strict = True)
    page_coverage_rate: float = Field(ge = 0, le = 1)
    source_reference_count: int = Field(ge = 0, strict = True)
    valid_source_reference_count: int = Field(ge = 0, strict = True)
    evidence_quote_count: int = Field(ge = 0, strict = True)
    supported_evidence_quote_count: int = Field(ge = 0, strict = True)
    unverified_lesson_count: int = Field(ge = 0, strict = True)
    uncovered_substantive_pages: list[int] = Field(default_factory = list)
    citation_integrity_passed: bool
    quote_support_passed: bool
    errors: list[str] = Field(default_factory = list)


class DistillRunManifest(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1", "1.2"] = versions.MANIFEST_SCHEMA_VERSION
    workflow_version: NonEmptyText = versions.WORKFLOW_VERSION
    prompt_version: NonEmptyText = versions.PROMPT_VERSION
    renderer_version: NonEmptyText = "1.0"
    model_name: NonEmptyText
    run_id: SafeSegment
    book_id: SafeSegment
    extraction_run_id: SafeSegment
    source_manifest_key: NonEmptyText
    source_manifest_sha256: Sha256Hex
    ingestion_ai_run_id: SafeSegment
    ingestion_ai_manifest_key: NonEmptyText
    ingestion_ai_manifest_sha256: Sha256Hex
    expected_page_count: int = Field(gt = 0, strict = True)
    selected_page_count: int | None = Field(default = None, ge = 1, strict = True)
    content_selection_key: NonEmptyText | None = None
    content_selection_sha256: Sha256Hex | None = None
    content_selection_revision: int | None = Field(default = None, ge = 1, strict = True)
    processed_book_key: NonEmptyText | None = None
    processed_book_sha256: Sha256Hex | None = None
    processed_book_revision: int | None = Field(default = None, ge = 1, strict = True)
    processed_page_count: int | None = Field(default = None, ge = 1, strict = True)
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    resume_from_node: str | None = None
    progress: DistillProgress = Field(default_factory = DistillProgress)
    report_key: NonEmptyText | None = None
    report_sha256: Sha256Hex | None = None
    html_key: NonEmptyText | None = None
    html_sha256: Sha256Hex | None = None
    pdf_key: NonEmptyText | None = None
    pdf_sha256: Sha256Hex | None = None
    structural_quality: StructuralQuality | None = None
    covered_page_count: int = 0
    uncovered_pages: list[int] = Field(default_factory = list)
    review_required: bool = True
    created_at: float = Field(gt = 0)
    updated_at: float = Field(gt = 0)
    finished_at: float | None = None
    error: str | None = None

    @model_validator(mode = "after")
    def _validate_terminal_state(self) -> DistillRunManifest:
        if self.schema_version == "1.1" and any(value is None for value in (
            self.selected_page_count,
            self.content_selection_key,
            self.content_selection_sha256,
            self.content_selection_revision,
        )):
            raise ValueError("1.1 Distill runs must pin their content-selection revision")
        if self.schema_version == "1.2" and any(value is None for value in (
            self.selected_page_count,
            self.content_selection_key,
            self.content_selection_sha256,
            self.content_selection_revision,
            self.processed_book_key,
            self.processed_book_sha256,
            self.processed_book_revision,
            self.processed_page_count,
        )):
            raise ValueError("1.2 Distill runs must pin the processed-book page set")
        if self.status in {"done", "failed", "cancelled"} and self.finished_at is None:
            raise ValueError("terminal Distill runs require finished_at")
        if self.status == "done" and (self.report_key is None or self.html_key is None):
            raise ValueError("completed Distill runs require report artifacts")
        if self.status == "failed" and not self.error:
            raise ValueError("failed Distill runs require an error")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        return self


ChapterSynthesis.model_rebuild()
ReportSection.model_rebuild()
