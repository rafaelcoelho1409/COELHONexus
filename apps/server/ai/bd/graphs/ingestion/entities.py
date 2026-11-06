"""Ingestion graph entities — what the nodes of one run need."""
from __future__ import annotations
from ... import graphs, nodes

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict


PageReader = Callable[[str], str | None | Awaitable[str | None]]
ArtifactWriter = Callable[[str, str, str], Any]


class PageReference(TypedDict):
    """Small manifest reference to one immutable raw Markdown page."""

    page: int
    key: str
    pictures: NotRequired[int]


class IngestionPageReference(TypedDict):
    """Pinned page inputs and output locations; never contains Markdown text."""

    page: int
    source_key: str
    source_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    pictures: NotRequired[int]


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


@dataclass(frozen = True, slots = True)
class IngestionDependencies:
    """What the nodes of one run need: where pages and artifacts live, the agents, and where progress goes."""

    read_page: PageReader
    write_artifact: ArtifactWriter
    auditor: nodes.ingestion.audit_batch.service.Auditor
    specialists: dict[str, nodes.ingestion.repair_pages.service.Specialist]
    metadata_normalizer: nodes.ingestion.metadata_normalization.service.Normalizer
    progress_callback: graphs.service.ProgressReporter | None = None
    finding_verifier: Any | None = None
