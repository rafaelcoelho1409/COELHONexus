"""mgsr — Pydantic schemas (the reviewer's decision and the persisted replan blob)."""
from __future__ import annotations
from . import versions

from typing import Literal, Optional

from pydantic import BaseModel, Field


HaltReason = Literal[
    "chapter_passed",          # pass_rate ≥ the threshold: nothing to review
    "reviewer_halt",           # the reviewer found no rewrite that would help: ship the best draft so far
    "targeted_revision",       # the reviewer named the sections to write again
    "full_rewrite",            # the reviewer named none: the whole chapter is written again
    "reviewer_unavailable",    # the reviewer failed: the whole chapter is written again
]


class Revision(BaseModel):
    """One section the next iteration writes again, and what it must fix."""
    section_id: str
    instruction: str


class MGSRDecision(BaseModel):
    """What the CoRefine loop does with the chapter: halt (render it) or rewrite the named sections / the whole chapter."""
    halt:              bool
    halt_reason:       HaltReason
    confidence:        float
    revisions:         list[Revision] = Field(default_factory = list)
    rationale_overall: str = ""


class MGSRReplan(BaseModel):
    """Full replan blob persisted to MinIO."""
    schema_version:           str = versions.MGSR_SCHEMA_VERSION
    prompt_version:           str = versions.MGSR_PROMPT_VERSION
    chapter_id:               str
    chapter_title:            str
    framework_slug:           str
    iteration:                int = 0
    decision:                 MGSRDecision
    deployment:               Optional[str] = None
    wall_ms:                  Optional[int] = None
    # Triggering input snapshot (for downstream debugging).
    checklist_pass_rate:      float
    checklist_chapter_passed: bool
    n_failed_criteria:        int
    failed_criteria_names:    list[str]
