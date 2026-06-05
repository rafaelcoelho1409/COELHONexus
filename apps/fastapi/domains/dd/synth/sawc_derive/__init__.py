"""sawc_derive — Analogical-Prompting + MPSC derived-code enrichment.

Ship #95 (2026-05-24). Runs AFTER sawc_write, BEFORE checklist_eval.

Public surface: the graph node + DeriveStats/DeriveAttempt schemas + the
small set of pure helpers (`python_ast_valid` is the one render-side
callers need).
"""
from .domain import (
    is_thin_block,
    parse_code_block,
    python_ast_valid,
    rank_mpsc_samples,
    score_derived_candidate,
)
from .node import sawc_derive
from .prompts import build_analogical_prompt, build_reexplain_prompt
from .schemas import DeriveAttempt, DeriveStats
from .versions import (
    SAWC_DERIVE_PROMPT_VERSION,
    SAWC_DERIVE_SCHEMA_VERSION,
)


__all__ = [
    "DeriveAttempt",
    "DeriveStats",
    "SAWC_DERIVE_PROMPT_VERSION",
    "SAWC_DERIVE_SCHEMA_VERSION",
    "build_analogical_prompt",
    "build_reexplain_prompt",
    "is_thin_block",
    "parse_code_block",
    "python_ast_valid",
    "rank_mpsc_samples",
    "sawc_derive",
    "score_derived_candidate",
]
