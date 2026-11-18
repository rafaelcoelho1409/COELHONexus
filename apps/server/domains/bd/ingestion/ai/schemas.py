"""Shared contracts and primitive types for Book Distiller AI ingestion."""
from __future__ import annotations
from . import patterns

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    model_validator,
)


SafeSegment = Annotated[str, StringConstraints(pattern = patterns.SAFE_SEGMENT_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern = patterns.SHA256_HEX_PATTERN)]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]
ObjectKey = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


def is_safe_segment(value: object) -> bool:
    """Return whether a value satisfies the storage-safe identity contract."""
    return isinstance(value, str) and bool(value) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


class ExtractionPage(BaseModel):
    """One page of the completed extraction, as the extraction manifest lists it."""

    model_config = ConfigDict(extra = "forbid")

    page: StrictInt = Field(gt = 0)
    key: str = Field(min_length = 1)
    pictures: StrictInt = Field(default = 0, ge = 0)
    figures: list[str] = Field(default_factory = list)

class ExtractionRequest(BaseModel):
    """A finished extraction run and its pages, in physical order, each at its canonical raw object key."""

    model_config = ConfigDict(extra = "forbid")

    book_id: SafeSegment
    extraction_run_id: SafeSegment
    extraction_status: Literal["done"]
    expected_page_count: StrictInt = Field(gt = 0)
    page_refs: list[ExtractionPage]

    @model_validator(mode = "after")
    def _validate_pages(self) -> ExtractionRequest:
        if len(self.page_refs) != self.expected_page_count:
            raise ValueError("page_refs must cover the expected physical page count")
        for expected_page, page_ref in enumerate(self.page_refs, start = 1):
            if page_ref.page != expected_page:
                raise ValueError("page_refs must list every page once in physical page order")
            expected_key = (
                f"bd/books/{self.book_id}/ingestion/raw/{self.extraction_run_id}/pages/"
                f"{page_ref.page:04d}.md"
            )
            if page_ref.key != expected_key:
                raise ValueError(f"page {page_ref.page} does not use its canonical raw object key")
        return self

    def page_references(self) -> list[dict[str, int | str | list[str]]]:
        """The pages as the plain dicts the graph state keeps."""
        return [page_ref.model_dump() for page_ref in self.page_refs]

__all__ = ["ExtractionPage", "ExtractionRequest", "NonEmptyText", "ObjectKey", "SafeSegment", "Sha256Hex", "is_safe_segment"]
