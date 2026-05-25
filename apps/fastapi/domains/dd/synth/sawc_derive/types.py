"""sawc_derive types — DeriveStats + per-subtopic outcome records."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .constants import SAWC_DERIVE_SCHEMA_VERSION, SAWC_DERIVE_PROMPT_VERSION


# =============================================================================
# Per-subtopic outcome — one row per derive attempt
# =============================================================================
class DeriveAttempt(BaseModel):
    """Per-subtopic record. Persisted for observability + replay.

    `decision`:
        "promoted"      → derived_code accepted, written back into sawc
                          subtopic, code_source flipped to "derived"
        "skipped_thin"  → not thin per the heuristic (signature wasn't
                          short enough) — left as verbatim
        "rejected_ast"  → all MPSC samples failed Python AST parse
        "rejected_len"  → no sample landed in the LOC band
        "rotator_fail" → bandit rotator returned no usable response
                          (rate-limit, timeout, content-filter)
        "disabled"      → KD_ENABLE_SAWC_DERIVE=false, skipped at node
    """
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


# =============================================================================
# Aggregate stats — the rendered "OUR ch- stats" object
# =============================================================================
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
    attempts:            list[DeriveAttempt] = Field(default_factory=list)
