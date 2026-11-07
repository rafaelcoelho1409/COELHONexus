"""DeepAgents of the synth stage: the answer contracts and one package per role."""
from __future__ import annotations

from . import params, schemas
from . import (
    chapter_judge, 
    chapter_outliner, 
    chapter_reviewer, 
    claim_extractor, 
    code_deriver, 
    code_normalizer, 
    coverage_extender,
    drift_detector, 
    paragraph_patcher, 
    section_writer, 
    source_extender, 
    source_router, 
    term_canonicalizer
)

__all__ = [
    "chapter_judge",
    "chapter_outliner",
    "chapter_reviewer",
    "claim_extractor",
    "code_deriver",
    "code_normalizer",
    "coverage_extender",
    "drift_detector",
    "paragraph_patcher",
    "params",
    "schemas",
    "section_writer",
    "source_extender",
    "source_router",
    "term_canonicalizer",
]
