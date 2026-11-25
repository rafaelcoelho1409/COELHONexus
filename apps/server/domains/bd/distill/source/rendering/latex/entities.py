"""Value objects exchanged by the LaTeX source builder and compiler."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReportAsset:
    """A report image with a generated, traversal-safe filename."""

    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class LatexDocument:
    """Pandoc JSON document plus local assets needed by its LaTeX output."""

    source_json: str
    assets: tuple[ReportAsset, ...]
