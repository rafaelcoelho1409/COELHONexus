"""mgsr — constants and tunables."""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
MGSR_SCHEMA_VERSION = "1.0"
MGSR_PROMPT_VERSION = "v1-2026-05-19"

_CONFIDENCE_HIGH_THRESHOLD = 0.85
_CONFIDENCE_PLATEAU_THRESHOLD = 0.70
_MAX_ACTIONS_PER_REPLAN = 10
_RATIONALE_MIN_CHARS = 20
_RATIONALE_MAX_CHARS = 400
_RATIONALE_OVERALL_MIN_CHARS = 50
_RATIONALE_OVERALL_MAX_CHARS = 800
_HEADING_MIN_WORDS = 2
_HEADING_MAX_WORDS = 8
_DESCRIPTION_MIN_CHARS = 20
_DESCRIPTION_MAX_CHARS = 400
_MIN_TARGETS = {
    "merge":   2,    # combining requires ≥2 sections
    "delete":  1,
    "rename":  1,
    "reorder": 1,
    "add":     0,    # `add` doesn't operate on existing targets
}
_MAX_TARGETS_PER_ACTION = 8

_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")
