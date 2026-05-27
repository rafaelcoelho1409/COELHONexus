"""outline_sdp — Structure-Driven Planner library.

Pure module: Pydantic schemas + DAG primitives + prompt templates +
deterministic validators. No I/O, no LLM calls — that lives in
`synth/outline/node.py`.

ARCHITECTURE — SurveyGen-I PlanEvo SDP (arXiv 2508.14317 §3.1)

A single LLM call per chapter produces a `ChapterOutline` containing
`OutlineSection`s with explicit `prerequisites`. From those prerequisites
the code (NOT the LLM) builds a DAG, breaks cycles by feedback-arc-set
edge removal, and assigns each section a `stage_index` equal to its
longest-path depth from a source. Same-stage sections are downstream-
parallelizable (sawc_write picks this up).

WHY THIS REPLACES THE DEPRECATED PHASE-A + PHASE-A.5 BUCKET-SPLIT

The deprecated outliner produced a flat list of 4-15 sections with a
prose `assumes_from_prior_sections` string and a separate heuristic
(`split_overloaded_sections`) that k-means-clustered hashes for any
section exceeding 10. Two problems: (1) prose dependencies are not
machine-readable, so MGSR replan had to re-parse them every iteration;
(2) bucket-split was a post-hoc patch for under-decomposition rather
than addressing the cause. Typed `prerequisites: list[section_id]` +
prompt instructions encouraging deeper-but-narrower sections fix both.

RESEARCH FOUNDATION (May 2026)

- SurveyGen-I (arXiv 2508.14317, Aug 2025) — primary architectural source
- OutlineForge (arXiv 2601.09858, Jan 2026) — structured edit actions on
  hierarchical outline state; informs `mgsr_replan`, not this module
- IterSurvey (arXiv 2510.21900, Oct 2025) — outline stability check
  `Sim(O_i, O_{i+1}) >= τ`; we use tree-edit-distance later in MGSR
- Meow (arXiv 2509.19370, Sep 2025) — confirms free-tier 8B (Qwen3-8B
  SFT+GRPO) is sufficient for outline; we use rotator pool of similar
  caliber (glm-4.6, qwen-3-coder-30b, llama-4-scout)
- Universal Self-Consistency (arXiv 2311.17311) — N samples + LLM judge
  picker; we use N=3 with structural rubric in `_build_usc_vote_prompt`

INPUTS / OUTPUTS

  Input  (per chapter):
    framework_slug         — e.g. "langchain-langgraph-deepagents"
    chapter_id             — e.g. "ch-03-runtime"
    chapter_title          — e.g. "Runtime"
    chapter_description    — 1-line goal from planner reduce
    n_vault_hashes         — estimated code-block count (rough)
    sources_concat_md      — normalized markdown of all assigned sources
                              (corpus_normalize already ran; vault sentinels
                              like `<code-ref hash=".."/>` may be present
                              but outline_sdp does NOT touch them — that's
                              digest_construct's job)

  Output (Pydantic-validated):
    ChapterOutline{sections, challenges, flashcards}
    plus DAG derivation (post-LLM, deterministic):
      edges        — list[(predecessor_id, successor_id)]
      stage_index  — {section_id: int}
      stages       — {int: [section_id, ...]}  (inverse of stage_index)
      removed_edges — list[(p, s)] for cycles broken via FAS
      max_stage    — int

KEY NUMBERS (tunable, calibrated against SurveyGen-I + our deprecated impl)

  _SECTIONS_MIN          = 4    (matches deprecated _OUTLINE_MIN_SECTIONS)
  _SECTIONS_MAX          = 40   (matches deprecated _OUTLINE_MAX_SECTIONS,
                                  post-2026-05-12 bump for monster chapters)
  _MAX_STAGE_DEPTH       = 4    (too-deep DAG = linearizing under
                                  decomposition; reject + retry)
  _MAX_PREREQS_PER_NODE  = 3    (LLM has trouble keeping more than 3
                                  cross-section contracts consistent;
                                  observed in deprecated A-phase Run-N
                                  audits where 4+ prereq strings became
                                  contradictory by Phase C)
  _CHALLENGES_MIN/MAX    = 5/10
  _FLASHCARDS_MIN/MAX    = 4/15  (post-2026-04-24 relax from 8 to 4)

BANNED HEADINGS (lowercase set; checked case-insensitively)

  introduction / overview / summary / conclusion / getting started /
  about / preface — these are content-types, not topics. The deprecated
  outliner's regex check is preserved here + a small expansion based on
  the SurveyGen-I §4 ablation noting that meta-section names correlate
  with lower STRUC scores (-0.18 mean across topics).
"""
from __future__ import annotations

import re


# =============================================================================
# Versioning + tunables
# =============================================================================
OUTLINE_SCHEMA_VERSION = "1.0"
OUTLINE_PROMPT_VERSION = "v1-2026-05-19"

_SECTIONS_MIN = 4
_SECTIONS_MAX = 40
_MAX_STAGE_DEPTH = 4

# CORR-3 Q1 (2026-05-26 evening) — adaptive outline section-count cap.
# Empirical: Browser Use Run 1 produced 41 H2 sections from 38 source
# documents — the outline LLM extrapolated topics with no source backing
# (e.g. "Two-factor authentication handling" with zero citations). Cap
# the H2 count proportionally to the source pool. Tunables:
#   floor: 3 sections (a chapter has to have some structure)
#   slope: roughly 1 section per 3 sources
#   ceiling: 15 sections (above which the chapter splits poorly anyway)
# Net: 38 sources → 12 sections; 252 sources (FastMCP) → 15 sections (cap).
# The validator (validate_outline_structure) treats violations as
# repair-loop feedback — soft reject, not hard fail.
_OUTLINE_ADAPTIVE_FLOOR    = 3
_OUTLINE_ADAPTIVE_CEILING  = 15
_OUTLINE_ADAPTIVE_DIVISOR  = 3


def max_h2_for_n_sources(n_sources: int) -> int:
    """Adaptive ceiling for outline section count. See section-count cap
    rationale above."""
    if n_sources <= 0:
        return _OUTLINE_ADAPTIVE_FLOOR
    return min(
        _OUTLINE_ADAPTIVE_CEILING,
        max(_OUTLINE_ADAPTIVE_FLOOR, n_sources // _OUTLINE_ADAPTIVE_DIVISOR),
    )


# CORR-3 Q3 (2026-05-26 evening) — fuzzy H2 dedup threshold. Sequence-
# matcher ratio above this on case-folded headings flags the pair as a
# near-duplicate (added to validator issues for repair). 0.85 catches
# "Click a submit button via CSS selector" / "Click submit button via
# CSS selector" (~0.94) without false-positiving on legitimately
# distinct headings like "Browser Initialization" / "Browser Disposal"
# (~0.45).
_OUTLINE_H2_FUZZY_DEDUP_THRESHOLD = 0.85
_MAX_PREREQS_PER_NODE = 3
_CHALLENGES_MIN = 5
_CHALLENGES_MAX = 10
_FLASHCARDS_MIN = 4
_FLASHCARDS_MAX = 15
_HEADING_MIN_WORDS = 2
_HEADING_MAX_WORDS = 8
_DESCRIPTION_MIN_CHARS = 20
_DESCRIPTION_MAX_CHARS = 400

# Banned (case-folded). Content-type names that the deprecated outliner
# rejected + a few additions from SurveyGen-I ablations.
_BANNED_HEADINGS_LC: frozenset[str] = frozenset({
    "introduction", "overview", "summary", "conclusion",
    "getting started", "about", "preface", "epilogue",
    "references", "acknowledgments", "appendix",
    "background", "related work", "future work",
})

_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")   # s1, s2, ..., s999

_BANNED_LIST_HUMAN = ", ".join(
    f"'{h.title()}'" for h in sorted(_BANNED_HEADINGS_LC)
)
