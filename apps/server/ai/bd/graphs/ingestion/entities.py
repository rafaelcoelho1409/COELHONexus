"""Ingestion graph entities — what the nodes of one run need."""
from __future__ import annotations
from ... import graphs, nodes

import collections.abc
import dataclasses
import typing


PageReader = collections.abc.Callable[[str], str | None | collections.abc.Awaitable[str | None]]
ArtifactWriter = collections.abc.Callable[[str, str, str], typing.Any]
ArtifactExists = collections.abc.Callable[[str], collections.abc.Awaitable[bool]]


class PageReference(typing.TypedDict):
    """Small manifest reference to one immutable raw Markdown page."""

    page: int
    key: str
    pictures: typing.NotRequired[int]
    section_path: typing.NotRequired[list[str]]


class IngestionPageReference(typing.TypedDict):
    """Pinned page inputs and output locations; never contains Markdown text."""

    page: int
    source_key: str
    source_sha256: str
    normalized_key: str
    assessment_key: str
    patch_proposals_key: str
    pictures: typing.NotRequired[int]
    section_path: typing.NotRequired[list[str]]


class IngestionPageAuditResult(typing.TypedDict):
    """One page's audit as kept in graph checkpoints."""

    page: int
    source_sha256: str
    markdown_chars: int
    pictures: int
    assessment: dict[str, object]
    warnings: typing.NotRequired[list[str]]
    audit_mode: typing.NotRequired[str]
    structured_output_fallback: typing.NotRequired[bool]


class IngestionPageResult(typing.TypedDict, total = False):
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


@dataclasses.dataclass(frozen = True, slots = True)
class IngestionDependencies:
    """What the nodes of one run need, handed to them as the graph's `context`: where pages and artifacts live, the agents, and where progress goes."""

    read_page: PageReader
    write_artifact: ArtifactWriter
    artifact_exists: ArtifactExists
    auditor: nodes.ingestion.audit_batch.service.Auditor
    specialists: dict[str, nodes.ingestion.repair_pages.service.Specialist]
    metadata_normalizer: nodes.ingestion.metadata_normalization.service.Normalizer
    content_classifier: nodes.ingestion.content_classification.service.ContentClassifier
    progress_callback: graphs.service.ProgressReporter | None = None
    finding_verifier: typing.Any | None = None
