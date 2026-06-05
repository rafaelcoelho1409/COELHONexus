"""mgsr — tunable scalars (CoRefine halting thresholds, schema bounds,
LLM call tuning)."""
from __future__ import annotations


# Halting cascade thresholds.
CONFIDENCE_HIGH_THRESHOLD = 0.85
CONFIDENCE_PLATEAU_THRESHOLD = 0.70
MAX_ACTIONS_PER_REPLAN = 10

# Per-action / per-section validation bounds.
RATIONALE_MIN_CHARS = 20
RATIONALE_MAX_CHARS = 400
RATIONALE_OVERALL_MIN_CHARS = 50
RATIONALE_OVERALL_MAX_CHARS = 800
HEADING_MIN_WORDS = 2
HEADING_MAX_WORDS = 8
DESCRIPTION_MIN_CHARS = 20
DESCRIPTION_MAX_CHARS = 400
MIN_TARGETS = {
    "merge":   2,    # combining requires ≥2 sections
    "delete":  1,
    "rename":  1,
    "reorder": 1,
    "add":     0,    # `add` doesn't operate on existing targets
}
MAX_TARGETS_PER_ACTION = 8

# Slow-path LLM call tuning.
TEMPERATURE_REPLAN  = 0.2     # mostly deterministic structural decisions
TEMPERATURE_REPAIR  = 0.0
MAX_TOKENS_REPLAN   = 4000
MAX_TOKENS_REPAIR   = 4000
MAX_REPAIR_ATTEMPTS = 1

BLOB_PREFIX = "synth"
