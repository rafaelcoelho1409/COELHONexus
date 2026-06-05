"""chapter_propose tunables — chapter-count bounds + LLM caps + cache tag."""
from __future__ import annotations

import os


# Chapter-count bounds (Pydantic-enforced ABSOLUTE limits). The per-corpus
# TARGET is adaptive (see `domain.target_chapters_for_n_docs`). The schema
# range is wide so the proposer can land anywhere the adaptive target
# points; the prompt + optimal-stopping floor steer it to the right count
# for the corpus.
PROPOSALS_MIN = 4
PROPOSALS_MAX = 30   # was 18 — capped large corpora into mega-chapters
                     # (LangChain 777 docs → 13 ch / 57 docs-per-ch).
                     # Raised so the adaptive target (≤24) is never
                     # schema-clipped.

# Adaptive chapter-count target (2026-05-31, DD-PLANNER-UNDERCHAPTERING).
# Previously the proposer aimed for a FIXED 4-18 regardless of corpus
# size, so big corpora got too few chapters → mega-chapters that bind the
# synth section ceiling (LangChain ch-01 = 184 docs / 10 sections / 18
# docs-per-section). Anchored to the corpora that decomposed WELL (~8-11
# docs/chapter):
#   browser-use 44 → ~5
#   langfuse 97 → ~9
#   claude-code 140 → 13 (exact)
#   langchain 777 → 24 (≈32 docs/ch, healthy) instead of 13 (57 docs/ch)
# Sub-linear via the ceiling so a huge corpus stays a sane book size.
PROPOSALS_DIVISOR = 11        # ~docs per chapter (anchors CC 140 → 13)
PROPOSALS_TARGET_FLOOR = 5
PROPOSALS_TARGET_CEILING = 24

# LLM context budget.
MAX_TOKENS_PROPOSE = 6000

# Sample N parallel proposals to mitigate single-arm variance, then
# USC-vote pick the best (matches reduce node's pattern).
N_SAMPLES = 3
MAX_TOKENS_VOTE = 200

TEMPERATURE_PROPOSE = 0.4   # diversity across samples
TEMPERATURE_VOTE    = 0.0

MAX_REPAIR_ATTEMPTS = 1

# V2 (2026-05-28) — Optimal-Stopping (CGES arXiv 2511.02603). Fire sample
# 0 first; if it parses cleanly AND emits ≥ OPTIMAL_STOPPING_MIN_PROPOSALS,
# skip remaining N-1 samples and ship it as-is. Saves ~67% of LLM calls
# in the best case (sample 0 clean). Same pattern as outline_sdp's
# `OUTLINE_OPTIMAL_STOPPING_*`.
#
# Absolute floor of 6; the NODE scales this up to ~0.7×(adaptive target)
# per corpus so a large corpus doesn't early-stop on a too-small sample-0
# (e.g. target 24 → effective floor ~17). Small corpora keep 6.
OPTIMAL_STOPPING_MIN_PROPOSALS = 6
OPTIMAL_STOPPING_ENABLED = (
    os.environ["KD_PROPOSE_OPTIMAL_STOPPING"].lower()
    in ("true", "1", "yes", "on")
)

# Per-doc body cap when feeding raw bodies (small-N pass-through).
# Generous but bounded so the total prompt stays within Cerebras 128K /
# Gemini 1M.
BODY_CHARS_PER_DOC = 2_000

# Structural seed extraction tuning.
SEED_MAX_HEADINGS = 60
SEED_MAX_NAMESPACES = 30

BLOB_PREFIX = "planner"

# Chapter title bounds.
TITLE_MIN_WORDS = 2
TITLE_MAX_WORDS = 8

# Description bounds.
DESCRIPTION_CHARS_MIN = 20
DESCRIPTION_CHARS_MAX = 400

# Key-concepts bounds per chapter.
CONCEPTS_MIN = 3
CONCEPTS_MAX = 15
CONCEPT_CHARS_MIN = 2
CONCEPT_CHARS_MAX = 80

# Headings the structural-seed scanner skips (boilerplate openings).
GENERIC_HEADINGS = frozenset({
    "introduction", "overview", "summary", "conclusion",
    "getting started", "about", "preface", "epilogue",
})
