"""Typed LangGraph state for Docs Distiller page ingestion."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict


class PageReference(TypedDict):
    """Immutable source reference and metadata for one normalized page."""

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


class IngestionState(TypedDict, total=False):
    """Graph state carries references and compact findings, never page Markdown."""

    framework_slug: str
    run_id: str
    pages: list[PageReference]
    assessments: list[PageAssessmentResult]
    output_pages: list[PageOutputReference]
    summary: dict[str, Any]
