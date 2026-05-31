from __future__ import annotations

import re


# Chapter-count bounds (Pydantic-enforced ABSOLUTE limits). The per-corpus
# TARGET is adaptive — see target_chapters_for_n_docs below. The schema range
# is wide so the proposer can land anywhere the adaptive target points; the
# prompt + optimal-stopping floor steer it to the right count for the corpus.
_PROPOSALS_MIN = 4
_PROPOSALS_MAX = 30   # was 18 — capped large corpora into mega-chapters
                      # (LangChain 777 docs → 13 ch / 57 docs-per-ch). Raised
                      # so the adaptive target (≤24) is never schema-clipped.

# Adaptive chapter-count target (2026-05-31, DD-PLANNER-UNDERCHAPTERING).
# Previously the proposer aimed for a FIXED 4-18 regardless of corpus size, so
# big corpora got too few chapters → mega-chapters that bind the synth section
# ceiling (LangChain ch-01 = 184 docs / 10 sections / 18 docs-per-section).
# Anchored to the corpora that decomposed WELL (~8-11 docs/chapter):
#   browser-use 44→~5, langfuse 97→~9, claude-code 140→13 (exact), and now
#   langchain 777→24 (≈32 docs/ch, healthy) instead of 13 (57 docs/ch).
# Sub-linear via the ceiling so a huge corpus stays a sane book size.
_PROPOSALS_DIVISOR = 11    # ~docs per chapter (anchors CC 140 → 13)
_PROPOSALS_TARGET_FLOOR = 5
_PROPOSALS_TARGET_CEILING = 24


def target_chapters_for_n_docs(n_docs: int) -> int:
    """Per-corpus target chapter count (guides the proposer + optimal-stopping
    floor). Clamped to [_PROPOSALS_TARGET_FLOOR, _PROPOSALS_TARGET_CEILING]."""
    if n_docs <= 0:
        return _PROPOSALS_TARGET_FLOOR
    return min(
        _PROPOSALS_TARGET_CEILING,
        max(_PROPOSALS_TARGET_FLOOR, round(n_docs / _PROPOSALS_DIVISOR)),
    )

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
# Absolute floor of 6; the NODE scales this up to ~0.7×(adaptive target) per
# corpus so a large corpus doesn't early-stop on a too-small sample-0 (e.g.
# target 24 → effective floor ~17). Small corpora keep 6.
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
# v2 (2026-05-31) — adaptive chapter-count target (target_chapters_for_n_docs)
# replaces the fixed 4-18, fixing under-chaptering of large corpora. Bumped so
# a re-plan re-proposes under the new target instead of cache-hitting the old
# mega-chapter set.
_PROMPT_VERSION = "v2-adaptive-count-2026-05-31"

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
