"""Ingestion graph entities — what the nodes of one run need."""
from __future__ import annotations
from .... import graphs

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NotRequired, Protocol, TypedDict


PageReader = Callable[[str], str | None | Awaitable[str | None]]
SourcePageImageReader = Callable[[int], bytes | None | Awaitable[bytes | None]]
SourceFigureReader = Callable[[str], bytes | None | Awaitable[bytes | None]]
ArtifactWriter = Callable[[str, str, str], Any]
ArtifactExists = Callable[[str], Awaitable[bool]]
CancelCheck = Callable[[], Awaitable[bool]]


class PageReference(TypedDict):
    """Small manifest reference to one immutable raw Markdown page."""

    page: int
    key: str
    pictures: NotRequired[int]
    figures: NotRequired[list[str]]
    section_path: NotRequired[list[str]]


class IngestionPageReference(TypedDict):
    """Pinned page inputs and output locations; never contains Markdown text."""

    page: int
    source_key: str
    source_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    enriched_key: NotRequired[str]
    visual_artifacts_key: NotRequired[str]
    pictures: NotRequired[int]
    figures: NotRequired[list[str]]
    section_path: NotRequired[list[str]]


class IngestionPageAuditResult(TypedDict):
    """One page's audit as kept in graph checkpoints."""

    page: int
    source_sha256: str
    markdown_chars: int
    pictures: int
    assessment: dict[str, object]
    warnings: NotRequired[list[str]]
    audit_mode: NotRequired[str]
    structured_output_fallback: NotRequired[bool]


class IngestionPageResult(TypedDict, total = False):
    """Compact page result safe to keep in graph checkpoints."""

    page: int
    source_sha256: str
    normalized_sha256: str
    assessment_sha256: str
    patch_proposals_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    assessment: dict[str, object]
    patch_proposal_count: int
    auto_applied_issue_ids: list[str]
    review_required_issue_ids: list[str]
    delivery_disposition: str
    repair_context_chars: int
    repair_call_count: int
    mechanical_fix_counts: dict[str, int]
    audit_warnings: list[str]
    status: str
    error: str
    content_role: str
    content_confidence: float
    content_rationale: str
    content_disposition: str
    content_decision_source: str
    repair_skipped_reason: str
    source_verified_issue_ids: list[str]
    source_verification_results: list[dict[str, object]]
    source_verification_unavailable_count: int
    enriched_key: str
    enriched_sha256: str
    visual_artifacts_key: str
    visual_artifacts_sha256: str
    converted_figures: list[str]


class AgentInvoker(Protocol):
    """Structured model interface used by ingestion graph stages."""

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class SpecialistInvoker(Protocol):
    """Specialist agent interface that accepts page repair context."""

    async def ainvoke(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
        *,
        context: Any | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen = True, slots = True)
class IngestionDependencies:
    """Runtime inputs for graph nodes: page and artifact access, agents, and progress reporting."""

    read_page: PageReader
    write_artifact: ArtifactWriter
    artifact_exists: ArtifactExists
    auditor: AgentInvoker
    specialists: dict[str, SpecialistInvoker]
    metadata_normalizer: AgentInvoker
    content_classifier: AgentInvoker
    progress_callback: graphs.service.ProgressReporter | None = None
    cancel_check: CancelCheck | None = None
    finding_verifier: Any | None = None
    patch_verifier: Any | None = None
    read_source_page_image: SourcePageImageReader | None = None
    visual_extractor: Any | None = None
    read_source_figure: SourceFigureReader | None = None
