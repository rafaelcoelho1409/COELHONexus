"""Bounded candidate contract for the ingestion contents-reconciliation graph node."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .. import schemas as ingestion_schemas


class ContentsPageCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_page_number: int = Field(gt = 0, strict = True)
    evidence: list[str] = Field(default_factory = list, max_length = 16)
    similarity: float = Field(default = 0.0, ge = 0, le = 1, allow_inf_nan = False)


class ProcessedHeadingCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_page_number: int = Field(gt = 0, strict = True)
    title: str = Field(min_length = 1, max_length = 300)
    level: int = Field(ge = 1, le = 8, strict = True)
    order: int = Field(ge = 0, strict = True)
    confidence: float = Field(ge = 0, le = 1, allow_inf_nan = False)
    provenance: Literal["section_path", "processed_page_heading"]


class ContentsEntryCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    outline_index: int = Field(ge = 0, strict = True)
    title: str = Field(min_length = 1, max_length = 300)
    level: int = Field(ge = 1, le = 8, strict = True)
    original_source_page_number: int = Field(gt = 0, strict = True)
    candidates: list[ContentsPageCandidate] = Field(min_length = 1, max_length = 8)


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
    pages: list[ingestion_schemas.ProcessedBookPage]
    heading_candidates: list[ProcessedHeadingCandidate] = Field(default_factory = list)
    heading_candidates_built: bool = False
    contents_candidates: list[ContentsEntryCandidate] = Field(default_factory = list)
    created_at: float = Field(gt = 0)
