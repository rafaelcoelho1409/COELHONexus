"""Contracts for classified and reviewed source-page selections."""
from __future__ import annotations
from .. import schemas

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class ContentSelectionPage(BaseModel):
    """Classifier result and effective inclusion policy for one original physical page."""

    model_config = ConfigDict(extra = "forbid")

    page_number: int = Field(gt = 0, strict = True)
    role: Literal["core", "preface", "reference", "navigation", "legal", "noise", "unknown"]
    confidence: float = Field(ge = 0, le = 1, allow_inf_nan = False)
    rationale: Annotated[str, StringConstraints(strip_whitespace = True, max_length = 600)]
    disposition: Literal["include", "exclude"]
    decision_source: Literal["classifier", "content_policy", "conservative_fallback", "user"]

class ContentSelectionManifest(BaseModel):
    """Append-only page-selection revision, independent from canonical source and QA artifacts."""

    model_config = ConfigDict(extra = "forbid")

    schema_version: Literal["1.0", "1.1"] = "1.1"
    classifier_version: schemas.NonEmptyText
    book_id: schemas.SafeSegment
    extraction_run_id: schemas.SafeSegment
    agentic_run_id: schemas.SafeSegment
    revision: int = Field(gt = 0, strict = True)
    expected_page_count: int = Field(gt = 0, strict = True)
    pages: list[ContentSelectionPage]
    created_at: float = Field(gt = 0, allow_inf_nan = False)

    @model_validator(mode = "after")
    def _validate_selection(self) -> ContentSelectionManifest:
        numbers = [page.page_number for page in self.pages]
        if numbers != list(range(1, self.expected_page_count + 1)):
            raise ValueError("content selection must include every physical page in order")
        return self

__all__ = ["ContentSelectionManifest", "ContentSelectionPage"]
