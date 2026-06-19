from __future__ import annotations

import re


# `(N)b` parameter-size form (case-insensitive), e.g. `llama-3.3-70b`.
PARAM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# MoE markers — `8x7b`, `128e`, `mixtral`, `moe`. MoE bypasses the capability floor.
MOE_RE = re.compile(
    r"\d+\s*x\s*\d+|\b\d+\s*x\b|\d+e\b|\bmoe\b|mixtral|mixture",
    re.IGNORECASE,
)

# Permanent-death phrases — match triggers runtime blocklist (bandit cooldown
# alone would retry a dead arm forever). Covers NIM 410/404, OpenAI/Anthropic
# model_not_found, and generic deprecated/decommissioned wording.
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
