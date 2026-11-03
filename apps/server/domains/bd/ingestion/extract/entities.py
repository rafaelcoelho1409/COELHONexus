from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Heading:
    level: int
    text: str


@dataclass(frozen=True, slots=True)
class Figure:
    """A picture extracted from a PDF page or EPUB section. `bbox` is None when the source has no page geometry."""
    page_no: int
    index: int
    name: str
    png: bytes
    bbox: tuple[float, float, float, float] | None
    caption: str


@dataclass(frozen=True, slots=True)
class PageExtract:
    page_no: int
    markdown: str
    headings: tuple[Heading, ...]
    tables: int
    pictures: int
    formulas: int
    figures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SliceResult:
    """One extraction call: its ordered content units, figures and structured document (image pixels left out)."""
    pages: tuple[PageExtract, ...]
    figures: tuple[Figure, ...]
    structured: dict | None
