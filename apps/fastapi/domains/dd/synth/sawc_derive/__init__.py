"""sawc_derive — Analogical-Prompting + MPSC derived-code enrichment.

Ship #95 (2026-05-24). Runs AFTER sawc_write, BEFORE checklist_eval.

Public surface (purely pure-function modules; the `node` entry point
is intentionally NOT re-exported here to avoid pulling LLM rotator
deps into render-side callers that only need `python_ast_valid`):
  - types:    DeriveStats, DeriveAttempt
  - service:  pure helpers (is_thin_block, python_ast_valid, …)
  - constants: tunables (_N_MPSC_SAMPLES, thin thresholds, env flags)

To wire the graph node, import directly: `from .sawc_derive.node import sawc_derive`.
"""
from .constants import (
    SAWC_DERIVE_SCHEMA_VERSION,
    SAWC_DERIVE_PROMPT_VERSION,
    _ENV_ENABLED,
    _DD_PROCESS,
    _N_MPSC_SAMPLES,
)
from .types import DeriveAttempt, DeriveStats
from .service import (
    is_thin_block,
    parse_code_block,
    python_ast_valid,
    score_derived_candidate,
    rank_mpsc_samples,
    build_analogical_prompt,
    build_reexplain_prompt,
)


__all__ = [
    "SAWC_DERIVE_SCHEMA_VERSION",
    "SAWC_DERIVE_PROMPT_VERSION",
    "_ENV_ENABLED",
    "_DD_PROCESS",
    "_N_MPSC_SAMPLES",
    "DeriveAttempt",
    "DeriveStats",
    "is_thin_block",
    "parse_code_block",
    "python_ast_valid",
    "score_derived_candidate",
    "rank_mpsc_samples",
    "build_analogical_prompt",
    "build_reexplain_prompt",
]
