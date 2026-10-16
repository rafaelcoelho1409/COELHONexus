"""outline_sdp — tunable section-count bounds, adaptive H2 cap, banned
headings, USC vote tuning, LLM call budgets."""
from __future__ import annotations

import math
import os


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


BANNED_LIST_HUMAN = ", ".join(
    f"'{h.title()}'" for h in sorted(BANNED_HEADINGS_LC)
)


# A chapter whose code the sections could not print: each section prints at most TOPUP_SECTION_MAX (16) blocks and the coverage gate asks for
# 75% of that, so the ceiling follows the block count too (fastmcp ch-01: 135 blocks and 18 sources -> 4 sections, 29 blocks printed).
OUTLINE_BLOCKS_PER_SECTION = 12
OUTLINE_BLOCK_CEILING      = 12
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

# Draft/vote/repair fan-out + LLM call budgets (moved from service.py —
# tunables live here, not beside the orchestration).
N_SAMPLES          = 3
TEMPERATURE_DRAFT  = 0.4
TEMPERATURE_VOTE   = 0.0
TEMPERATURE_REPAIR = 0.2
MAX_REPAIR_RETRIES = 2
MAX_TOKENS_DRAFT   = 8000
MAX_TOKENS_VOTE    = 200
MAX_TOKENS_REPAIR  = 8000

# chat_text_async's own default (30s) was undersized for these calls —
# confirmed live: outline_sdp's repair loop timed out on nearly every
# chapter across 5 study runs (2026-09-05/07), routinely trimming
# outlines down as a fallback rather than actually repairing them.
# Scaled to each call's max_tokens, same idiom as render's existing
# timeout_s=60.0 override.
TIMEOUT_S_DRAFT  = 120.0
TIMEOUT_S_VOTE   = 45.0
TIMEOUT_S_REPAIR = 120.0

# Draft-call attempts per sample. chat_text_async runs with max_retries=0, so a
# single dropped connection used to forfeit the sample outright; two of the
# three samples died that way on elasticsearch-python ch-01 (APIConnectionError,
# same instant) and, with the third sample rejected on a heading, the chapter
# fell through to the heuristic fallback. Same idiom as sawc_write's draft calls.
MAX_CALL_ATTEMPTS = 2

# Tag carried by every section description the heuristic fallback emits, so a
# cached fallback outline is recognisable (and re-drafted) on the next run.
FALLBACK_DESCRIPTION_TAG = "synthesized as fallback after LLM outline generation failed"

OPTIMAL_STOPPING_ENABLED = os.environ.get(
    "KD_OUTLINE_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

SCOPE_LEXICAL_JACCARD = 0.40

# Threshold for service._detect_semantic_h2_duplicates (used as a default
# arg there, evaluated at def time — importing params first is enough).
SEMANTIC_H2_DEDUP_THRESHOLD = 0.74
