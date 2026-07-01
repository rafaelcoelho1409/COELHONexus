"""sawc_derive — Analogical-Prompting + MPSC derived-code enrichment; runs after sawc_write, before checklist_eval."""
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
