"""Canonical processed-book contracts and contents-reconciliation candidates."""
from __future__ import annotations
from . import params
from .. import schemas

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProcessedBookPage(BaseModel):
    """A selected, densely numbered page with its immutable physical source identity."""

    model_config = ConfigDict(extra = "forbid")

    processed_page_number: int = Field(gt = 0, strict = True)
    source_page_number: int = Field(gt = 0, strict = True)
    source_key: schemas.ObjectKey
    source_sha256: schemas.Sha256Hex
    content_key: schemas.ObjectKey
    content_sha256: schemas.Sha256Hex
    section_path: list[str] = Field(default_factory = list)
    role: Literal["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]

class ProcessedContentsEntry(BaseModel):
    """One canonical contents item with hierarchy, page mapping, and source provenance."""

    model_config = ConfigDict(extra = "forbid")

    entry_id: str = Field(default = "", max_length = 64)
    parent_entry_id: str | None = Field(default = None, max_length = 64)
    level: int = Field(ge = 1, le = 8, strict = True)
    title: schemas.NonEmptyText = Field(max_length = 300)
    processed_page_number: int = Field(gt = 0, strict = True)
    source_page_number: int = Field(gt = 0, strict = True)
    confidence: float = Field(ge = 0, le = 1, allow_inf_nan = False)
    review_required: bool = False
    provenance: Literal[
        "source_outline",
        "processed_page_heading",
        "section_path",
        "source_outline_and_page_heading",
        "source_outline_and_section_path",
    ] = "source_outline"
    source_outline_index: int | None = Field(default = None, ge = 0, strict = True)

class ProcessedBookManifest(BaseModel):
    """Canonical processed book page set pinned to one content-selection revision."""

    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0"] = "1.0"
    book_id: schemas.SafeSegment
    extraction_run_id: schemas.SafeSegment
    agentic_run_id: schemas.SafeSegment
    selection_key: schemas.ObjectKey
    selection_sha256: schemas.Sha256Hex
    selection_revision: int = Field(gt = 0, strict = True)
    expected_source_page_count: int = Field(gt = 0, strict = True)
    source_page_count: int = Field(gt = 0, strict = True)
    pages: list[ProcessedBookPage]
    contents: list[ProcessedContentsEntry] = Field(default_factory = list)
    contents_status: Literal["complete", "needs_review", "unavailable"]
    contents_agent_version: schemas.NonEmptyText
    created_at: float = Field(gt = 0, allow_inf_nan = False)

    @model_validator(mode = "after")
    def _validate_processed_book(self) -> ProcessedBookManifest:
        processed_numbers = [page.processed_page_number for page in self.pages]
        source_numbers = [page.source_page_number for page in self.pages]
        if processed_numbers != list(range(1, len(self.pages) + 1)):
            raise ValueError("processed pages must have contiguous numbers starting at one")
        if source_numbers != sorted(set(source_numbers)):
            raise ValueError("processed page source identities must be unique and ordered")
        if self.source_page_count != len(self.pages):
            raise ValueError("source_page_count must match the processed page set")
        included = set(source_numbers)
        page_map = {page.source_page_number: page.processed_page_number for page in self.pages}
        if any(entry.source_page_number not in included for entry in self.contents):
            raise ValueError("contents entries must point to included source pages")
        if any(page_map[entry.source_page_number] != entry.processed_page_number for entry in self.contents):
            raise ValueError("contents page numbers must match the processed page map")
        entries_by_id = {}
        for entry in self.contents:
            if not entry.entry_id:
                continue
            if entry.entry_id in entries_by_id:
                raise ValueError("contents entry identifiers must be unique")
            if entry.parent_entry_id is not None:
                parent = entries_by_id.get(entry.parent_entry_id)
                if parent is None or parent.level >= entry.level:
                    raise ValueError("contents parent identifiers must refer to an earlier, higher-level entry")
            entries_by_id[entry.entry_id] = entry
        return self

class ContentsPageCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_page_number: int = Field(ge = params.MIN_SOURCE_PAGE_NUMBER, strict = True)
    evidence: list[str] = Field(default_factory = list, max_length = params.MAX_HEADING_EVIDENCE)
    similarity: float = Field(
        default = params.MIN_NORMALIZED_SCORE,
        ge = params.MIN_NORMALIZED_SCORE,
        le = params.MAX_NORMALIZED_SCORE,
        allow_inf_nan = False,
    )

class ProcessedHeadingCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_page_number: int = Field(ge = params.MIN_SOURCE_PAGE_NUMBER, strict = True)
    title: str = Field(min_length = 1, max_length = params.MAX_TITLE_CHARS)
    level: int = Field(ge = params.MIN_HEADING_LEVEL, le = params.MAX_HEADING_LEVEL, strict = True)
    order: int = Field(ge = params.MIN_ZERO_BASED_INDEX, strict = True)
    confidence: float = Field(
        ge = params.MIN_NORMALIZED_SCORE,
        le = params.MAX_NORMALIZED_SCORE,
        allow_inf_nan = False,
    )
    provenance: Literal["section_path", "processed_page_heading"]

class ContentsEntryCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    outline_index: int = Field(ge = params.MIN_ZERO_BASED_INDEX, strict = True)
    title: str = Field(min_length = 1, max_length = params.MAX_TITLE_CHARS)
    level: int = Field(ge = params.MIN_HEADING_LEVEL, le = params.MAX_HEADING_LEVEL, strict = True)
    original_source_page_number: int = Field(ge = params.MIN_SOURCE_PAGE_NUMBER, strict = True)
    candidates: list[ContentsPageCandidate] = Field(
        min_length = 1,
        max_length = params.MAX_CONTENTS_PAGE_CANDIDATES,
    )

class ProcessedBookCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1"] = "1.1"
    book_id: str
    extraction_run_id: str
    agentic_run_id: str
    selection_key: str
    selection_sha256: str
    selection_revision: int = Field(gt = 0, strict = True)
    expected_source_page_count: int = Field(gt = 0, strict = True)
    pages: list[ProcessedBookPage]
    heading_candidates: list[ProcessedHeadingCandidate] = Field(default_factory = list)
    heading_candidates_built: bool = False
    contents_candidates: list[ContentsEntryCandidate] = Field(default_factory = list)
    created_at: float = Field(gt = 0)

__all__ = ["ContentsEntryCandidate", "ContentsPageCandidate", "ProcessedBookCandidate", "ProcessedBookManifest", "ProcessedBookPage", "ProcessedContentsEntry", "ProcessedHeadingCandidate"]
