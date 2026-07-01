"""sawc_derive — Pydantic schemas (DeriveAttempt + DeriveStats)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .versions import (
    SAWC_DERIVE_PROMPT_VERSION,
    SAWC_DERIVE_SCHEMA_VERSION,
)


class DeriveAttempt(BaseModel):
    """Per-subtopic derive record; decision: 'promoted'|'skipped_thin'|'rejected_ast'|'rejected_len'|'rotator_fail'|'disabled'."""
    section_id:        str
    subheading:        str
    code_ref_hash:     str
    original_chars:    int
    original_lines:    int
    decision: Literal[
        "promoted", "skipped_thin", "rejected_ast",
        "rejected_len", "rotator_fail", "disabled",
    ]
    derived_chars:     Optional[int] = None
    derived_lines:     Optional[int] = None
    n_samples_tried:   int = 0
    n_samples_valid:   int = 0
    chosen_sample_idx: Optional[int] = None
    deployment:        Optional[str] = None
    wall_ms:           int = 0


class DeriveStats(BaseModel):
    """Per-chapter aggregate stats. Persisted to sawc_derive-latest.json
    and surfaced in the synth UI as KPIs."""
    schema_version:      str = SAWC_DERIVE_SCHEMA_VERSION
    prompt_version:      str = SAWC_DERIVE_PROMPT_VERSION
    chapter_id:          str
    framework_slug:      str
    enabled:             bool
    n_subtopics_total:   int
    n_candidates_thin:   int       # passed signature/length heuristic
    n_promoted:          int       # derived_code persisted onto subtopic
    n_rejected_ast:      int
    n_rejected_len:      int
    n_rotator_fail:      int
    wall_ms:             int
    attempts:            list[DeriveAttempt] = Field(default_factory = list)
