"""outline_sdp — tunable section-count bounds, adaptive H2 cap, banned
headings, USC vote tuning."""
from __future__ import annotations


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
