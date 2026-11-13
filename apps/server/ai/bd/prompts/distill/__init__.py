"""Versioned prompts for Book Distiller learning-guide roles."""
from __future__ import annotations

from . import (
    book_synthesizer,
    chapter_synthesizer,
    evidence_verifier,
    learning_extractor,
)

__all__ = [
    "book_synthesizer",
    "chapter_synthesizer",
    "evidence_verifier",
    "learning_extractor",
]
