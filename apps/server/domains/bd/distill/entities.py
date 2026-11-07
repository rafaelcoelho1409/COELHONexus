"""Internal references used by the Distill graph and its persisted artifacts."""
from __future__ import annotations

from typing import NotRequired, TypedDict


class SourcePage(TypedDict):
    page_number: int
    source_key: str
    source_sha256: str
    section_path: list[str]
    disposition: str
    review_required: bool


class UnitRef(TypedDict):
    unit_id: str
    input_key: str
    page_number: int
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
    unit_ids: list[str]
    existing_artifact: NotRequired[bool]
