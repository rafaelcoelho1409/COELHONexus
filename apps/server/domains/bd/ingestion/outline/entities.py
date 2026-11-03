from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    """One entry in the book outline: `level` 1 is top-level, `page` is a 1-based reader-unit number."""
    level: int
    title: str
    page: int


@dataclass(frozen=True, slots=True)
class PdfInfo:
    """What the file says about itself, read before any page is extracted."""
    n_pages: int
    title: str
    outline: tuple[OutlineEntry, ...]


@dataclass(frozen=True, slots=True)
class EpubInfo:
    """EPUB package metadata read before Docling converts its ordered content documents."""
    title: str
