"""Typed artifact references and runtime contexts for BD Distill."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict


@dataclass(frozen = True, slots = True)
class DistillRetrievalContext:
    """Runtime identity and accounting data passed to Distill source retrievers."""

    book_id: str
    run_id: str
    model: Any = None
    counter_run_id: str | None = None
    counter_node_prefix: str = "distill_retrieval"


class SourcePage(TypedDict):
    page_number: int
    processed_page_number: NotRequired[int]
    source_key: str
    source_sha256: str
    section_path: list[str]
    disposition: str
    review_required: bool
    content_role: NotRequired[str]
    figures: NotRequired[list[str]]


class UnitRef(TypedDict):
    unit_id: str
    input_key: str
    page_number: int
    processed_page_number: NotRequired[int]
    section_path: list[str]
    source_sha256: str
    text_sha256: str
    char_start: int
    char_end: int
    existing_artifact: NotRequired[bool]
    learning_unit_ids: NotRequired[list[str]]


class ChapterRef(TypedDict):
    chapter_id: str
    artifact_key: str
    input_key: str
    section_path: list[str]
    part_index: int
    first_page: int
    last_page: int
    unit_ids: list[str]
    existing_artifact: NotRequired[bool]
    unit_sources: NotRequired[list[dict[str, str]]]
