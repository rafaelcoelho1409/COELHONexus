"""chapter_propose tunables — chapter-count bounds + LLM caps + cache tag."""
from __future__ import annotations

import os


# Schema range wide so the adaptive target can land anywhere; prompt + optimal-stopping floor steer the final count.
PROPOSALS_MIN = 4
PROPOSALS_MAX = 30   # was 18 — capped large corpora into mega-chapters
                     # (LangChain 777 docs → 13 ch / 57 docs-per-ch).
                     # Raised so the adaptive target (≤24) is never
                     # schema-clipped.

# Adaptive target: fixed 4-18 caused mega-chapters on large corpora (LangChain 777 docs → 57 docs/ch instead of 32).
PROPOSALS_DIVISOR = 11        # ~docs per chapter (anchors CC 140 → 13)
PROPOSALS_TARGET_FLOOR = 5
PROPOSALS_TARGET_CEILING = 24

# LLM context budget. 6000→4000 cuts TTFT ~30% on free-tier (tianpan) while still fits 24 ch×400ch.
MAX_TOKENS_PROPOSE = 4000

# Sample N parallel proposals to mitigate single-arm variance, then
# USC-vote pick the best (matches reduce node's pattern).
N_SAMPLES = 3
MAX_TOKENS_VOTE = 200

TEMPERATURE_PROPOSE = 0.4   # diversity across samples
TEMPERATURE_VOTE    = 0.0

MAX_REPAIR_ATTEMPTS = 1

# Optimal-stopping (CGES 2511.02603): node scales floor to ~0.7×adaptive_target so large corpora don't early-stop on a small sample-0.
# SOTA Sept 2026: with coelho-llm-rotator pooled http2 (200/100) 3×6000 tok drafts
# run concurrently ~1× latency vs 2× sequential s0→remaining. Old default true
# was cost-saving for bandit cascade; for fastest, default false (env override
# keeps cost mode). ReASC/Blend-ASC papers show parallel best-of-N + USC
# scales best when pooled.
OPTIMAL_STOPPING_MIN_PROPOSALS = 6
OPTIMAL_STOPPING_ENABLED = (
    os.getenv("KD_PROPOSE_OPTIMAL_STOPPING", "false").lower()
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
