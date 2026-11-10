"""outline_sdp — tunable section-count bounds, adaptive H2 cap, banned
headings, USC vote tuning, LLM call budgets."""
from __future__ import annotations

import math


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "outline_sdp"


# see versions.py rationale.
SECTIONS_MIN = 2
SECTIONS_MAX = 40
MAX_STAGE_DEPTH = 4

# Adaptive outline section-count cap.
OUTLINE_ADAPTIVE_FLOOR    = 2
OUTLINE_ADAPTIVE_CEILING  = 10
OUTLINE_ADAPTIVE_DIVISOR  = 4

# fuzzy H2 dedup threshold.
OUTLINE_H2_FUZZY_DEDUP_THRESHOLD = 0.85

MAX_PREREQS_PER_NODE = 3
# Min 1: API-reference chapters are organised by module / namespace /
# resource names that are legitimately one word ('Aggregations', 'Fleet',
# 'Migration'). A 2-word floor rejected the WHOLE outline over one such
# heading — confirmed live on elasticsearch-python (ch-01/05/11/16 each lost
# a sample; ch-01 lost all three and shipped the heuristic fallback).
# Generic one-worders ('Overview', 'Introduction', ...) stay out via
# BANNED_HEADINGS_LC.
HEADING_MIN_WORDS = 1
HEADING_MAX_WORDS = 8
DESCRIPTION_MIN_CHARS = 20
DESCRIPTION_MAX_CHARS = 400

# Content-type names that the deprecated outliner rejected.
BANNED_HEADINGS_LC: frozenset[str] = frozenset({
    "introduction", "overview", "summary", "conclusion",
    "getting started", "about", "preface", "epilogue",
    "references", "acknowledgments", "appendix",
    "background", "related work", "future work",
})



# A chapter whose code the sections could not print: each section prints at most TOPUP_SECTION_MAX (16) blocks and the coverage gate asks for
# 75% of that, so the ceiling follows the block count too (fastmcp ch-01: 135 blocks and 18 sources -> 4 sections, 29 blocks printed).
OUTLINE_BLOCKS_PER_SECTION = 12
OUTLINE_BLOCK_CEILING      = 12
# Tutorial sections group related canonical examples. Keep each code bank small
# enough for one writer pass while avoiding one section per code family.
TUTORIAL_CODE_EXAMPLES_PER_SECTION = 12
# …and a FLOOR: the model ignores the target (fastmcp ch-01: 4 sections for 135 blocks in 3 runs), so an outline with fewer sections than
# ceil(blocks / OUTLINE_BLOCKS_PER_SECTION_FLOOR) (at most the cap) is a validation issue the repair loop sends back.
OUTLINE_BLOCKS_PER_SECTION_FLOOR = 20
SECTION_SUBTOPIC_MAX = 16     # what one section can show (mirrors sawc TOPUP_SECTION_MAX); named in the floor issue so the model sees why


def min_h2_for_n_blocks(n_blocks: int, cap: int) -> int:
    """Fewest sections a code-heavy chapter may have (0 = no floor); never above the cap."""
    if n_blocks <= 0:
        return 0
    need = math.ceil(n_blocks / OUTLINE_BLOCKS_PER_SECTION_FLOOR)
    return min(cap, need) if need > SECTIONS_MIN else 0


def max_h2_for_n_sources(n_sources: int, n_blocks: int = 0) -> int:
    """Adaptive ceiling for outline section count: from the source pool, and, for a code-heavy chapter, from its code-block count."""
    if n_sources <= 0:
        base = OUTLINE_ADAPTIVE_FLOOR
    else:
        base = min(
            OUTLINE_ADAPTIVE_CEILING,
            max(
                OUTLINE_ADAPTIVE_FLOOR,
                n_sources // OUTLINE_ADAPTIVE_DIVISOR,
            ),
        )
    if n_blocks <= 0:
        return base
    return max(base, min(OUTLINE_BLOCK_CEILING, math.ceil(n_blocks / OUTLINE_BLOCKS_PER_SECTION)))


BLOB_PREFIX = "synth"

# Outliner runs per chapter: the first, and one more with what the first left wrong (or after a context overflow, at half the source index).
MAX_ATTEMPTS = 2
FLOOR_REPAIR_EXTRA = 2          # extra attempts while the code-block section floor is still unmet
REPAIR_FLOOR_SLACK = 2          # a retry that clears the section floor is kept even with up to this many more issues than before (see domain.accept_repair)
# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0

# What the outliner sees of each source before it opens one.
INDEX_HEADINGS = 12
INDEX_EXCERPT_CHARS = 300

# Tag carried by every section description the heuristic fallback emits, so a
# cached fallback outline is recognisable (and re-drafted) on the next run.
FALLBACK_DESCRIPTION_TAG = "synthesized as fallback after LLM outline generation failed"

# Prefix of the validation issue raised when a code-heavy chapter has fewer
# sections than its block floor (domain.is_floor_issue).
FLOOR_ISSUE_PREFIX = "Outline has only "

# Per-source body cap for the concatenated prompt context. service.py's
# overflow-retry path also reads this (halves the budget on a context-overflow
# batch before re-drafting).
MAX_SOURCE_CHARS = 180_000

# Separator between concatenated source bodies.
SOURCE_CONCAT_SEPARATOR = "\n\n---\n\n"

# Stop words dropped when measuring lexical scope overlap between a section
# and a source (domain.scope_words) — lightly stemmed, so the stems are here.
SCOPE_STOPWORDS = frozenset({
    "the", "and", "for", "this", "with", "that", "from", "section", "show",
    "how", "use", "using", "via", "your", "each", "into", "onto", "claude",
    "code", "example", "demonstrate", "learn", "cover", "when", "what",
    "where", "which", "while", "they", "them", "then", "here", "run",
    "creat", "make", "field", "valu", "option", "config", "setup", "set",
})

CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)

SCOPE_LEXICAL_JACCARD = 0.40

# Threshold for service._detect_semantic_h2_duplicates (used as a default
# arg there, evaluated at def time — importing params first is enough).
SEMANTIC_H2_DEDUP_THRESHOLD = 0.74
