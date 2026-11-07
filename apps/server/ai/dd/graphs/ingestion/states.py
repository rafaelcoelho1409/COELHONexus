"""Typed LangGraph state for Docs Distiller page ingestion."""
from __future__ import annotations
from . import entities

from typing import Any, TypedDict


class IngestionState(TypedDict, total = False):
    """Graph state carries references and compact findings, never page Markdown."""

    framework_slug: str
    run_id: str
    pages: list[entities.PageReference]
    assessments: list[entities.PageAssessmentResult]
    output_pages: list[entities.PageOutputReference]
    summary: dict[str, Any]
