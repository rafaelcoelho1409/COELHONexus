"""Validated agent, manifest, source, and Learning Guide contracts."""
from __future__ import annotations

import json
import math
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from . import versions

SafeSegment = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+$")]
FigureName = Annotated[str, StringConstraints(pattern = r"^[A-Za-z0-9_-]+\.png$")]
Sha256Hex = Annotated[str, StringConstraints(pattern = r"^[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]

_JSON_ARRAY_FIELDS = frozenset({
    "action_plan",
    "assumptions",
    "charts",
    "columns",
    "coordinates",
    "diagrams",
    "edges",
    "equations",
    "evidence",
    "lessons",
    "learning_unit_ids",
    "learning_objectives",
    "objective_ids",
    "external_source_ids",
    "nodes",
    "omitted_material",
    "open_questions",
    "overview_learning_unit_ids",
    "practice_items",
    "rows",
    "transformations",
    "timelines",
    "events",
    "features",
    "maps",
    "options",
    "source_figures",
    "research_queries",
    "research_sources",
    "y_fields",
})
_JSON_OBJECT_ARRAY_FIELDS = frozenset({
    "action_plan",
    "charts",
    "columns",
    "diagrams",
    "edges",
    "equations",
    "evidence",
    "lessons",
    "learning_objectives",
    "practice_items",
    "nodes",
    "open_questions",
    "rows",
    "timelines",
    "events",
    "features",
    "maps",
    "options",
    "source_figures",
    "research_sources",
})
_JSON_OBJECT_FIELDS = frozenset({"dataset", "decision_brief", "geojson", "geometry", "properties", "values"})
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


class TimelineEvent(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    label: NonEmptyText = Field(max_length = 180)
    description: NonEmptyText = Field(max_length = 800)
    date_or_order: NonEmptyText = Field(max_length = 80)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 8)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class TimelineSpec(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 180)
    accessible_description: NonEmptyText = Field(max_length = 600)
    events: list[TimelineEvent] = Field(min_length = 2, max_length = 32)


class ChapterLesson(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 180)
    explanation: NonEmptyText = Field(max_length = 3000)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 12)


class LearningObjective(BaseModel):
    """A measurable chapter outcome linked to the verified source lessons that teach it."""

    model_config = ConfigDict(extra = "forbid")

    objective_id: SafeSegment
    outcome: NonEmptyText = Field(max_length = 500)
    kind: Literal["recall", "explain", "distinguish", "apply"]
    importance: int = Field(ge = 1, le = 3, strict = True)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 12)


class PracticeItem(BaseModel):
    """A retrieval or transfer exercise with a supported answer and corrective feedback."""

    model_config = ConfigDict(extra = "forbid")

    practice_id: SafeSegment
    question: NonEmptyText = Field(max_length = 1000)
    answer: NonEmptyText = Field(max_length = 1800)
    feedback: NonEmptyText = Field(max_length = 1200)
    kind: Literal["recall", "explain", "compare", "apply"]
    difficulty: int = Field(ge = 1, le = 5, strict = True)
    objective_ids: list[SafeSegment] = Field(min_length = 1, max_length = 8)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 12)


class SelectedSourceFigure(BaseModel):
    """A book figure selected only when its printed caption is traceable to source text."""

    model_config = ConfigDict(extra = "forbid")

    figure_name: FigureName
    page_number: int = Field(gt = 0, strict = True)
    caption: NonEmptyText = Field(max_length = 500)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 12)


class ChapterSynthesis(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    overview: NonEmptyText = Field(max_length = 4000)
    overview_attribution: Literal["book", "agent_inference"] = "book"
    overview_learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 24)
    lessons: list[ChapterLesson] = Field(max_length = 24)
    open_questions: list[ChapterLesson] = Field(max_length = 8)
    learning_objectives: list[LearningObjective] = Field(default_factory = list, max_length = 12)
    practice_items: list[PracticeItem] = Field(default_factory = list, max_length = 16)
    source_figures: list[SelectedSourceFigure] = Field(default_factory = list, max_length = 3)
    diagrams: list[DiagramSpec] = Field(default_factory = list, max_length = 8)
    equations: list[EquationBlock] = Field(default_factory = list, max_length = 8)
    charts: list[ChartSpec] = Field(default_factory = list, max_length = 8)
    timelines: list[TimelineSpec] = Field(default_factory = list, max_length = 4)
    maps: list[GeoMapSpec] = Field(default_factory = list, max_length = 4)

    @model_validator(mode = "before")
    @classmethod
    def _unwrap_stringified_collections(cls, data):
        """Normalize JSON-stringified nested collections before validating every field."""
        if isinstance(data, dict):
            data = {key: value for key, value in data.items() if key != "illustration_brief"}
        return _normalize_stringified_collections(data)

    @model_validator(mode = "after")
    def _validate_learning_contract(self) -> ChapterSynthesis:
        objective_ids = [item.objective_id for item in self.learning_objectives]
        practice_ids = [item.practice_id for item in self.practice_items]
        if len(objective_ids) != len(set(objective_ids)):
            raise ValueError("chapter objective IDs must be unique")
        if len(practice_ids) != len(set(practice_ids)):
            raise ValueError("chapter practice IDs must be unique")
        allowed_objective_ids = set(objective_ids)
        if any(set(item.objective_ids) - allowed_objective_ids for item in self.practice_items):
            raise ValueError("chapter practice items must reference supplied objective IDs")
        return self


class BookSynthesisOutputLesson(BaseModel):
    """Compact model output for whole-book orientation, separate from persisted schema limits."""
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 100)
    explanation: NonEmptyText = Field(max_length = 320)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)


class BookDecisionOption(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 120)
    expected_outcome: NonEmptyText = Field(max_length = 400)
    attribution: Literal["book", "agent_inference"] = "agent_inference"
    tradeoffs: list[NonEmptyText] = Field(default_factory = list, max_length = 5)
    risks: list[NonEmptyText] = Field(default_factory = list, max_length = 5)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)


class BookActionStep(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    sequence: int = Field(ge = 1, le = 12, strict = True)
    action: NonEmptyText = Field(max_length = 300)
    rationale: NonEmptyText = Field(max_length = 400)
    attribution: Literal["book", "agent_inference"] = "agent_inference"
    prerequisites: list[NonEmptyText] = Field(default_factory = list, max_length = 5)
    success_measure: NonEmptyText = Field(max_length = 240)
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)


class BookDecisionBrief(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    decision_question: NonEmptyText = Field(max_length = 300)
    recommendation: NonEmptyText = Field(max_length = 600)
    recommendation_attribution: Literal["book", "agent_inference"] = "agent_inference"
    recommendation_learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)
    options: list[BookDecisionOption] = Field(default_factory = list, max_length = 3)
    assumptions: list[NonEmptyText] = Field(default_factory = list, max_length = 8)
    risks: list[NonEmptyText] = Field(default_factory = list, max_length = 8)
    action_plan: list[BookActionStep] = Field(default_factory = list, max_length = 8)

    @model_validator(mode = "after")
    def _validate_action_sequence(self) -> BookDecisionBrief:
        sequence = [step.sequence for step in self.action_plan]
        if sequence != sorted(set(sequence)):
            raise ValueError("decision action steps must use unique, ascending sequence numbers")
        return self


class DecisionBriefOutput(BaseModel):
    """Nullable decision brief from its focused DeepAgent role."""

    model_config = ConfigDict(extra = "forbid")

    decision_brief: BookDecisionBrief | None = None


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
    decision_brief: BookDecisionBrief | None = None

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
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)


class ReportLearningObjective(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    objective_id: SafeSegment
    outcome: NonEmptyText
    kind: Literal["recall", "explain", "distinguish", "apply"]
    importance: int = Field(ge = 1, le = 3, strict = True)
    source_refs: list[SourceRef] = Field(min_length = 1)


class ReportPracticeItem(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    practice_id: SafeSegment
    question: NonEmptyText
    answer: NonEmptyText
    feedback: NonEmptyText
    kind: Literal["recall", "explain", "compare", "apply"]
    difficulty: int = Field(ge = 1, le = 5, strict = True)
    objective_ids: list[SafeSegment] = Field(min_length = 1, max_length = 8)
    source_refs: list[SourceRef] = Field(min_length = 1)


class ReportSourceFigure(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    figure_name: FigureName
    page_number: int = Field(gt = 0, strict = True)
    caption: NonEmptyText = Field(max_length = 500)
    image_sha256: Sha256Hex
    source_refs: list[SourceRef] = Field(min_length = 1)


class ReportDecisionOption(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    expected_outcome: NonEmptyText
    tradeoffs: list[NonEmptyText] = Field(default_factory = list)
    risks: list[NonEmptyText] = Field(default_factory = list)
    attribution: Literal["book", "agent_inference"] = "agent_inference"
    source_refs: list[SourceRef] = Field(min_length = 1)
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)


class ReportActionStep(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    sequence: int = Field(ge = 1, le = 12, strict = True)
    action: NonEmptyText
    rationale: NonEmptyText
    attribution: Literal["book", "agent_inference"] = "agent_inference"
    prerequisites: list[NonEmptyText] = Field(default_factory = list)
    success_measure: NonEmptyText
    source_refs: list[SourceRef] = Field(min_length = 1)
    external_source_ids: list[SafeSegment] = Field(default_factory = list, max_length = 6)


class DecisionBrief(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    decision_question: NonEmptyText
    recommendation: ReportLesson
    options: list[ReportDecisionOption] = Field(default_factory = list, max_length = 3)
    assumptions: list[NonEmptyText] = Field(default_factory = list)
    risks: list[NonEmptyText] = Field(default_factory = list)
    action_plan: list[ReportActionStep] = Field(default_factory = list, max_length = 8)


class ResearchSource(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_id: SafeSegment
    provider_id: Literal["coelho_search", "parallel"]
    title: NonEmptyText = Field(max_length = 240)
    url: NonEmptyText = Field(max_length = 2048)
    snippet: NonEmptyText = Field(max_length = 1200)
    query: NonEmptyText = Field(max_length = 400)
    publisher: str | None = Field(default = None, max_length = 160)
    published_at: str | None = Field(default = None, max_length = 80)
    accessed_at: float = Field(gt = 0)
    full_text_sha256: Sha256Hex | None = None
    full_text_char_count: int | None = Field(default = None, ge = 1, le = 12000, strict = True)
    full_text_accessed_at: float | None = Field(default = None, gt = 0)

    @field_validator("url")
    @classmethod
    def _require_safe_https_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("research source URLs must use HTTPS without embedded credentials")
        return value

    @model_validator(mode = "after")
    def _validate_full_text_provenance(self) -> ResearchSource:
        provenance = (self.full_text_sha256, self.full_text_char_count, self.full_text_accessed_at)
        if any(value is not None for value in provenance) and any(value is None for value in provenance):
            raise ValueError("full-text hash, character count, and access time must be recorded together")
        if self.full_text_accessed_at is not None and self.full_text_accessed_at < self.accessed_at:
            raise ValueError("full-text access time cannot precede search access time")
        return self


class ResearchEvidenceQuote(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_id: SafeSegment
    quote: NonEmptyText = Field(max_length = 1200)
    locator: str | None = Field(default = None, max_length = 240)


class ResearchFinding(BaseModel):
    """A concise external-context finding linked only to retrieved MCP sources."""

    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 180)
    summary: NonEmptyText = Field(max_length = 900)
    relevance_to_book: NonEmptyText = Field(max_length = 600)
    source_ids: list[SafeSegment] = Field(min_length = 1, max_length = 6)
    relation_to_book: Literal["supports", "updates", "challenges", "contextualizes"] = "contextualizes"
    evidence_quotes: list[ResearchEvidenceQuote] = Field(default_factory = list, max_length = 8)


class ResearchContext(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    status: Literal["disabled", "unavailable", "unsupported_provider", "no_results", "complete"]
    provider_id: str | None = None
    queries: list[NonEmptyText] = Field(default_factory = list, max_length = 3)
    sources: list[ResearchSource] = Field(default_factory = list, max_length = 12)
    findings: list[ResearchFinding] = Field(default_factory = list, max_length = 8)
    error_type: str | None = Field(default = None, max_length = 80)


class ResearchAnalysisOutput(BaseModel):
    """Structured output for the optional, separately sourced research appendix."""

    model_config = ConfigDict(extra = "forbid")

    findings: list[ResearchFinding] = Field(default_factory = list, max_length = 8)


class ReportReviewFinding(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    severity: Literal["note", "warning", "critical"]
    area: Literal["coverage", "clarity", "consistency", "evidence", "visuals", "decision_brief"]
    issue: NonEmptyText = Field(max_length = 500)
    recommendation: NonEmptyText = Field(max_length = 500)


class ReportReviewOutput(BaseModel):
    """Non-mutating editorial review; deterministic source validation remains authoritative."""

    model_config = ConfigDict(extra = "forbid")

    findings: list[ReportReviewFinding] = Field(default_factory = list, max_length = 12)


class ReportTimelineEvent(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    label: NonEmptyText
    description: NonEmptyText
    date_or_order: NonEmptyText
    attribution: Literal["book", "agent_inference"] = "book"
    source_refs: list[SourceRef] = Field(min_length = 1)


class ReportTimeline(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    accessible_description: NonEmptyText
    events: list[ReportTimelineEvent] = Field(min_length = 2)


class ReportSection(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText
    chapter_id: SafeSegment | None = None
    overview: NonEmptyText
    overview_attribution: Literal["book", "agent_inference"] = "book"
    overview_source_refs: list[SourceRef] = Field(min_length = 1)
    lessons: list[ReportLesson]
    open_questions: list[ReportLesson] = Field(default_factory = list)
    learning_objectives: list[ReportLearningObjective] = Field(default_factory = list, max_length = 12)
    practice_items: list[ReportPracticeItem] = Field(default_factory = list, max_length = 16)
    source_figures: list[ReportSourceFigure] = Field(default_factory = list, max_length = 3)
    coverage_review_status: Literal["unreviewed", "assistant_reviewed", "review_required", "unavailable"] = "unreviewed"
    coverage_review_notes: list[NonEmptyText] = Field(default_factory = list, max_length = 8)
    diagrams: list[DiagramSpec] = Field(default_factory = list)
    equations: list[EquationBlock] = Field(default_factory = list)
    charts: list[ChartSpec] = Field(default_factory = list)
    timelines: list[ReportTimeline] = Field(default_factory = list)
    maps: list[GeoMapSpec] = Field(default_factory = list, max_length = 4)

    @model_validator(mode = "before")
    @classmethod
    def _discard_legacy_illustration(cls, data):
        """Ignore illustration metadata from reports created before image generation was removed."""
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key != "illustration"}
        return data


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


Longitude = Annotated[float, Field(ge = -180, le = 180, allow_inf_nan = False)]
Latitude = Annotated[float, Field(ge = -90, le = 90, allow_inf_nan = False)]
GeoPosition = tuple[Longitude, Latitude]
LineCoordinates = Annotated[list[GeoPosition], Field(min_length = 2, max_length = 2_000)]
LinearRing = Annotated[list[GeoPosition], Field(min_length = 4, max_length = 2_000)]


class GeoJSONPoint(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["Point"]
    coordinates: GeoPosition


class GeoJSONMultiPoint(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["MultiPoint"]
    coordinates: list[GeoPosition] = Field(min_length = 1, max_length = 500)


class GeoJSONLineString(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["LineString"]
    coordinates: LineCoordinates


class GeoJSONMultiLineString(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["MultiLineString"]
    coordinates: list[LineCoordinates] = Field(min_length = 1, max_length = 100)


class GeoJSONPolygon(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["Polygon"]
    coordinates: list[LinearRing] = Field(min_length = 1, max_length = 50)

    @model_validator(mode = "after")
    def _require_closed_rings(self) -> GeoJSONPolygon:
        if any(ring[0] != ring[-1] for ring in self.coordinates):
            raise ValueError("GeoJSON polygon rings must be closed")
        return self


class GeoJSONMultiPolygon(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["MultiPolygon"]
    coordinates: list[list[LinearRing]] = Field(min_length = 1, max_length = 100)

    @model_validator(mode = "after")
    def _require_closed_rings(self) -> GeoJSONMultiPolygon:
        if any(ring[0] != ring[-1] for polygon in self.coordinates for ring in polygon):
            raise ValueError("GeoJSON polygon rings must be closed")
        return self


GeoJSONGeometry = Annotated[
    GeoJSONPoint | GeoJSONMultiPoint | GeoJSONLineString | GeoJSONMultiLineString | GeoJSONPolygon | GeoJSONMultiPolygon,
    Field(discriminator = "type"),
]


class GeoJSONFeatureProperties(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    label: NonEmptyText = Field(max_length = 180)
    description: str = Field(default = "", max_length = 800)
    attribution: Literal["book", "agent_inference"] = "book"
    learning_unit_ids: list[SafeSegment] = Field(min_length = 1, max_length = 8)
    evidence: list[EvidenceQuote] = Field(min_length = 1, max_length = 8)


class GeoJSONFeature(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["Feature"] = "Feature"
    id: SafeSegment
    properties: GeoJSONFeatureProperties
    geometry: GeoJSONGeometry


class GeoJSONFeatureCollection(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature] = Field(min_length = 1, max_length = 100)

    @model_validator(mode = "after")
    def _require_unique_feature_ids(self) -> GeoJSONFeatureCollection:
        feature_ids = [feature.id for feature in self.features]
        if len(feature_ids) != len(set(feature_ids)):
            raise ValueError("GeoJSON feature IDs must be unique")
        coordinate_count = sum(
            _geojson_coordinate_count(feature.geometry.coordinates)
            for feature in self.features
        )
        if coordinate_count > 10_000:
            raise ValueError("GeoJSON feature collections are limited to 10,000 coordinates")
        return self


def _geojson_coordinate_count(value: Any) -> int:
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            return 1
        return sum(_geojson_coordinate_count(item) for item in value)
    return 0


class GeoMapSpec(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    title: NonEmptyText = Field(max_length = 180)
    accessible_description: NonEmptyText = Field(max_length = 600)
    geojson: GeoJSONFeatureCollection


class VisualArtifactOutput(BaseModel):
    """Optional visual representations grounded in the supplied learning units."""

    model_config = ConfigDict(extra = "forbid")

    diagrams: list[DiagramSpec] = Field(default_factory = list, max_length = 8)
    equations: list[EquationBlock] = Field(default_factory = list, max_length = 8)
    charts: list[ChartSpec] = Field(default_factory = list, max_length = 8)
    timelines: list[TimelineSpec] = Field(default_factory = list, max_length = 4)
    maps: list[GeoMapSpec] = Field(default_factory = list, max_length = 4)
    source_figures: list[SelectedSourceFigure] = Field(default_factory = list, max_length = 3)

    @model_validator(mode = "before")
    @classmethod
    def _discard_legacy_illustration_brief(cls, data):
        """Ignore illustration briefs emitted by older Distill prompts."""
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key != "illustration_brief"}
        return data


class StudyGuideDocument(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"] = versions.REPORT_SCHEMA_VERSION
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
    maps: list[GeoMapSpec] = Field(default_factory = list, max_length = 4)
    decision_brief: DecisionBrief | None = None
    research_context: ResearchContext = Field(default_factory = lambda: ResearchContext(status = "disabled"))
    covered_pages: list[int]
    uncovered_pages: list[int]
    review_required: bool


class DistillNodeProgress(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    completed_count: int = Field(default = 0, ge = 0, strict = True)
    total_count: int = Field(default = 0, ge = 0, strict = True)
    active_count: int = Field(default = 0, ge = 0, strict = True)


class DistillProgress(BaseModel):
    current_node: str | None = None
    current_task: str | None = Field(default = None, max_length = 240)
    active_tasks: dict[str, str] = Field(default_factory = dict)
    node_status: dict[str, str] = Field(default_factory = dict)
    node_progress: dict[str, DistillNodeProgress] = Field(default_factory = dict)
    completed_extractions: int = 0
    completed_units: int = 0
    total_units: int = 0
    completed_chapter_guides: int = 0
    completed_chapter_visuals: int = 0
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

    schema_version: Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6"] = versions.MANIFEST_SCHEMA_VERSION
    workflow_version: NonEmptyText = versions.WORKFLOW_VERSION
    prompt_version: NonEmptyText = versions.PROMPT_VERSION
    renderer_version: NonEmptyText = versions.RENDERER_VERSION
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
    research_enabled: bool = False
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    celery_task_id: NonEmptyText | None = None
    resume_from_node: str | None = None
    replay_from_node: str | None = None
    replay_target_node: str | None = None
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
        if self.schema_version in {"1.1", "1.2", "1.3", "1.4", "1.5", "1.6"} and any(value is None for value in (
            self.selected_page_count,
            self.content_selection_key,
            self.content_selection_sha256,
            self.content_selection_revision,
        )):
            raise ValueError("Distill runs from schema 1.1 onward must pin their content-selection revision")
        if self.schema_version in {"1.2", "1.3", "1.4", "1.5", "1.6"} and any(value is None for value in (
            self.selected_page_count,
            self.content_selection_key,
            self.content_selection_sha256,
            self.content_selection_revision,
            self.processed_book_key,
            self.processed_book_sha256,
            self.processed_book_revision,
            self.processed_page_count,
        )):
            raise ValueError("Distill runs from schema 1.2 onward must pin the processed-book page set")
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
