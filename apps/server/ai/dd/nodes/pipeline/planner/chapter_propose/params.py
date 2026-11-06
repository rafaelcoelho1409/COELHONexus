"""chapter_propose tunables — target sizing, corpus sharding, the architect's score gate, structural-signal bounds."""
from __future__ import annotations
from ..... import agents

import os


# Chapter-count bounds are part of the agents' contract (`agents.pipeline.planner.params`); the node reads the same numbers.
PROPOSALS_MIN = agents.pipeline.planner.params.PROPOSALS_MIN
PROPOSALS_MAX = agents.pipeline.planner.params.PROPOSALS_MAX
GENERIC_HEADINGS = frozenset({
    "introduction", "overview", "summary", "conclusion",
    "getting started", "about", "preface", "epilogue",
})

PROPOSALS_DIVISOR = 11        # ~docs per chapter (anchors CC 140 → 13)
PROPOSALS_TARGET_FLOOR = 5
PROPOSALS_TARGET_CEILING = 24

# Corpus sharding: a corpus of at least SHARD_CORPUS_MIN pages is read by one section analyst per URL directory (a directory
# under SHARD_MIN_PAGES joins its neighbour, one over SHARD_MAX_PAGES is cut into runs); a smaller corpus goes straight to the
# architect. ANALYST_CONCURRENCY analysts run at once.
SHARD_CORPUS_MIN = 120
SHARD_MIN_PAGES = 12
SHARD_MAX_PAGES = 120
ANALYST_CONCURRENCY = 4
ANALYST_ATTEMPTS = 2
ANALYST_RETRY_WAIT_S = 20.0

# The architect is asked again, with what the score reported, while its outline scores below WEAK_SCORE (at most
# ARCHITECT_ROUNDS asks in all); the best outline seen is kept.
ARCHITECT_ROUNDS = 2
ARCHITECT_ATTEMPTS = 2
ARCHITECT_RETRY_WAIT_S = 20.0
WEAK_SCORE = 0.8
# Uncovered pages quoted back to the architect on a retry.
FEEDBACK_MAX_PAGES = 12

# Settle window before the node's first model call: the Rotator's pool needs time to recover from the previous node's burst.
# SETTLE_DELAY_S is the cap; the wait ends as soon as the pool has recovered.
SETTLE_DELAY_S = 130.0

# A fallback on a corpus this large is a near-certain predictor of a throwaway plan: abort when enabled.
ABORT_MIN_DOCS = 50
ABORT_ON_DEGRADE_ENABLED = (
    os.environ.get("DD_PLANNER_ABORT_ON_DEGRADE", "").strip().lower()
    in ("1", "true", "yes", "on")
)

# Evidence score of an outline (`domain.score_outline`).
SCORE_MIN_MATCH     = 0.05   # cosine below this = the doc matches no chapter
SCORE_SHARE_CAP     = 0.30
SCORE_SHARE_WEIGHT  = 1.2
SCORE_COUNT_WEIGHT  = 0.6
SCORE_DOC_CHARS     = 600    # per-doc text used for matching (summary + terms + page slug)
SCORE_STOP = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "your", "into", "via", "are",
    "use", "how", "you", "can", "will", "not", "but", "its", "has", "using", "guide",
    "overview", "reference", "documentation", "covering", "usage", "concepts", "chapter",
    "page", "docs", "describes", "explains", "provides", "learn", "includes", "section",
})

BODY_CHARS_PER_DOC = 2_000

# Structural signals (`domain.build_source_structure`, `domain.extract_structural_seeds`).
SOURCE_STRUCTURE_MAX_LINES = 60
SECTION_MIN_DOCS  = 6
SECTION_MAX_SHARE = 0.35
SECTION_MAX_LINES = 12
SECTIONS_HEADER = "  Site sections (URL directories holding many pages):"
TRUNK_MIN_DOCS    = 20
TRUNK_MIN_GROUP   = 4
TRUNK_SPLIT_ABOVE = 14
TRUNK_MAX_LINES   = 18
TRUNK_MIN_COVER   = 0.5      # share of the directory its listed groups must cover, else it is not listed
SEED_MAX_HEADINGS = 60
SEED_MAX_NAMESPACES = 30
