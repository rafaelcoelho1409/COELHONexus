"""System prompts of the synth agents, one folder per role."""
from __future__ import annotations

from . import capstone_designer, chapter_judge, chapter_outliner, chapter_reviewer, claim_extractor, code_deriver, code_normalizer, coverage_extender
from . import drift_detector, paragraph_patcher, section_writer, source_extender, source_router, term_canonicalizer

__all__ = [
    "capstone_designer",
    "chapter_judge",
    "chapter_outliner",
    "chapter_reviewer",
    "claim_extractor",
    "code_deriver",
    "code_normalizer",
    "coverage_extender",
    "drift_detector",
    "paragraph_patcher",
    "section_writer",
    "source_extender",
    "source_router",
    "term_canonicalizer",
]
