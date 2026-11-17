"""Versioned prompts for Book Distiller learning-guide roles."""
from __future__ import annotations

from . import (
    book_synthesizer,
    chapter_synthesizer,
    decision_brief_writer,
    evidence_verifier,
    learning_extractor,
    report_reviewer,
    research_analyst,
    visual_artifact_builder,
)

__all__ = [
    "book_synthesizer",
    "chapter_synthesizer",
    "decision_brief_writer",
    "evidence_verifier",
    "learning_extractor",
    "report_reviewer",
    "research_analyst",
    "visual_artifact_builder",
]
