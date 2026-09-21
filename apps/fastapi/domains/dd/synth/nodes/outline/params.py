"""outline_sdp — tunable section-count bounds, adaptive H2 cap, banned
headings, USC vote tuning, LLM call budgets."""
from __future__ import annotations

import os as _os


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
HEADING_MIN_WORDS = 2
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


def max_h2_for_n_sources(n_sources: int) -> int:
    """Adaptive ceiling for outline section count."""
    if n_sources <= 0:
        return OUTLINE_ADAPTIVE_FLOOR
    return min(
        OUTLINE_ADAPTIVE_CEILING,
        max(
            OUTLINE_ADAPTIVE_FLOOR,
            n_sources // OUTLINE_ADAPTIVE_DIVISOR,
        ),
    )


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

OPTIMAL_STOPPING_ENABLED = _os.environ.get(
    "KD_OUTLINE_OPTIMAL_STOPPING", "true",
).lower() in ("true", "1", "yes", "on")

SCOPE_LEXICAL_JACCARD = 0.40

# Threshold for service._detect_semantic_h2_duplicates (used as a default
# arg there, evaluated at def time — importing params first is enough).
SEMANTIC_H2_DEDUP_THRESHOLD = 0.74
