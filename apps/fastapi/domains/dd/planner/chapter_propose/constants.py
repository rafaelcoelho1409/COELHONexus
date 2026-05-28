from __future__ import annotations

import re


# Chapter-count bounds (Pydantic-enforced).
_PROPOSALS_MIN = 4
_PROPOSALS_MAX = 18

# LLM context budget.
_MAX_TOKENS_PROPOSE = 6000

# Sample N parallel proposals to mitigate single-arm variance, then
# USC-vote pick the best (matches reduce node's pattern).
_N_SAMPLES = 3
_MAX_TOKENS_VOTE = 200

_TEMPERATURE_PROPOSE = 0.4   # diversity across samples
_TEMPERATURE_VOTE    = 0.0

_MAX_REPAIR_ATTEMPTS = 1

# V2 (2026-05-28) — Optimal-Stopping (CGES arXiv 2511.02603). Fire
# sample 0 first; if it parses cleanly AND emits ≥ _OPTIMAL_STOPPING_MIN
# proposals, skip remaining N-1 samples and ship it as-is. Saves ~67%
# of LLM calls in the best case (sample 0 clean). Same pattern as
# outline_sdp's `_OUTLINE_OPTIMAL_STOPPING_*`.
#
# Floor of 6 because the LLM-first planner aims for 6-15 chapters
# (chapter_propose schema allows 4-18; below 6 we'd rather fan out for
# a chance at richer coverage). Tuneable per corpus.
_OPTIMAL_STOPPING_MIN_PROPOSALS = 6
import os as _os
_OPTIMAL_STOPPING_ENABLED = _os.environ.get(
    "KD_PROPOSE_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

# Per-doc body cap when feeding raw bodies (small-N pass-through). Generous
# but bounded so the total prompt stays within Cerebras 128K / Gemini 1M.
_BODY_CHARS_PER_DOC = 2_000

# Structural seed extraction tuning.
_SEED_MAX_HEADINGS = 60
_SEED_MAX_NAMESPACES = 30

# CLI command-pattern detection — a doc whose path matches one of these
# patterns is treated as a CLI subcommand and gets a structural seed.
_CLI_PATTERN_RE = re.compile(
    r"(?:commands?|subcommands?|cli)/([a-z][a-z0-9-]*)",
    re.IGNORECASE,
)

_BLOB_PREFIX = "planner"
_PROMPT_VERSION = "v1-2026-05-27"

# Chapter title bounds.
_TITLE_MIN_WORDS = 2
_TITLE_MAX_WORDS = 8

# Description bounds.
_DESCRIPTION_CHARS_MIN = 20
_DESCRIPTION_CHARS_MAX = 400

# Key-concepts bounds per chapter.
_CONCEPTS_MIN = 3
_CONCEPTS_MAX = 15
_CONCEPT_CHARS_MIN = 2
_CONCEPT_CHARS_MAX = 80
