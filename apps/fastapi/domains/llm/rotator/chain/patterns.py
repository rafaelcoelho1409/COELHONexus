from __future__ import annotations

import re


PARAM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# MoE bypasses the capability floor.
MOE_RE = re.compile(
    r"\d+\s*x\s*\d+|\b\d+\s*x\b|\d+e\b|\bmoe\b|mixtral|mixture",
    re.IGNORECASE,
)

# Bandit cooldown alone would retry a dead arm forever; these phrases trigger permanent blocklist.
_EOL_PHRASES: tuple[str, ...] = (
    "reached its end of life",
    "end of life",
    "has been deprecated",
    "is deprecated",
    "no longer available",
    "no longer supported",
    "model_not_found",
    "not found for account",
    "model decommissioned",
    "decommissioned",
)
