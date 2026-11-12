"""Ingestion graph entities — what the nodes of one run need and the references its state carries."""
from __future__ import annotations
from ... import graphs, nodes

import asyncio
import collections
import dataclasses
import typing


class PageReference(typing.TypedDict):
    """Immutable source reference and metadata for one normalized page."""

    idx: int
    slug: str
    url: str
    source_path: typing.NotRequired[str]
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
    mechanical_fixes: typing.NotRequired[dict[str, int]]


class PageAssessmentResult(typing.TypedDict):
    """Compact assessment result safe to retain in the graph checkpoint."""

    idx: int
    source_sha256: str
    assessment: dict[str, typing.Any]
    mechanical_fixes: typing.NotRequired[dict[str, int]]
    warnings: typing.NotRequired[list[str]]
    audit_status: typing.NotRequired[str]
    audit_error: typing.NotRequired[str]
    patches: typing.NotRequired[dict[str, typing.Any]]
    normalization_status: typing.NotRequired[str]
    output_key: typing.NotRequired[str]
    output_sha256: typing.NotRequired[str]
    output_title: typing.NotRequired[str]


class PageOutputReference(typing.TypedDict):
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
    repair_status: typing.NotRequired[str]


@dataclasses.dataclass
class IngestionTally:
    """How many pages each stage of one run has finished; the stages' parallel page tasks add to it under `lock`."""

    completed_pages: int = 0
    completed_by_node: collections.Counter[str] = dataclasses.field(default_factory = collections.Counter)
    lock: asyncio.Lock = dataclasses.field(default_factory = asyncio.Lock)


@dataclasses.dataclass(frozen = True, slots = True)
class IngestionDependencies:
    """What the nodes of one run need: where pages and artifacts live, the agents, and where progress goes."""

    read_page: nodes.ingestion.audit_batch.service.PageReader
    artifact_exists: nodes.ingestion.audit_batch.service.ArtifactExists
    read_artifact: nodes.ingestion.audit_batch.service.ArtifactReader
    write_artifact: nodes.ingestion.audit_batch.service.ArtifactWriter
    auditor: nodes.ingestion.audit_batch.service.Auditor
    repairer: nodes.ingestion.normalize_pages.service.Repairer
    progress: graphs.service.ProgressReporter | None = None
    tally: IngestionTally = dataclasses.field(default_factory = IngestionTally)
