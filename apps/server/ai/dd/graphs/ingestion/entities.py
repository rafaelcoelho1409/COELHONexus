"""Ingestion graph entities — what the nodes of one run need and the references its state carries."""
from __future__ import annotations
from ... import graphs, nodes

import asyncio
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, NotRequired, TypedDict


class PageReference(TypedDict):
    """Immutable source reference and metadata for one normalized page."""

    idx: int
    slug: str
    url: str
    source_path: NotRequired[str]
    tier: str
    title: str
    section: str
    notes: str
    lastmod: str
    anchors: list[str]
    bytes: int
    source_sha256: str
    source_key: str
    raw_key: str


class PageAssessmentResult(TypedDict):
    """Compact assessment result safe to retain in the graph checkpoint."""

    idx: int
    source_sha256: str
    assessment: dict[str, Any]
    warnings: NotRequired[list[str]]
    audit_status: NotRequired[str]
    audit_error: NotRequired[str]
    patches: NotRequired[dict[str, Any]]
    normalization_status: NotRequired[str]
    output_key: NotRequired[str]
    output_sha256: NotRequired[str]
    output_title: NotRequired[str]


class PageOutputReference(TypedDict):
    """Published page reference; page Markdown remains in MinIO."""

    idx: int
    slug: str
    url: str
    tier: str
    title: str
    section: str
    notes: str
    lastmod: str
    anchors: list[str]
    bytes: int
    source_sha256: str
    source_key: str
    raw_key: str
    key: str
    assessment_key: str
    repair_status: NotRequired[str]


@dataclass
class IngestionTally:
    """How many pages each stage of one run has finished; the stages' parallel page tasks add to it under `lock`."""

    completed_pages: int = 0
    completed_by_node: Counter[str] = field(default_factory = Counter)
    lock: asyncio.Lock = field(default_factory = asyncio.Lock)


@dataclass(frozen = True, slots = True)
class IngestionDependencies:
    """What the nodes of one run need: where pages and artifacts live, the agents, and where progress goes."""

    read_page: nodes.ingestion.audit_batch.service.PageReader
    artifact_exists: nodes.ingestion.audit_batch.service.ArtifactExists
    read_artifact: nodes.ingestion.audit_batch.service.ArtifactReader
    write_artifact: nodes.ingestion.audit_batch.service.ArtifactWriter
    auditor: nodes.ingestion.audit_batch.service.Auditor
    repairer: nodes.ingestion.normalize_pages.service.Repairer
    progress: graphs.service.ProgressReporter | None = None
    tally: IngestionTally = field(default_factory = IngestionTally)
