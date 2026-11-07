"""checklist_eval — tunable thresholds + LLM-judge criterion names."""
from __future__ import annotations


# The name this stage reports its progress events, cached items and scratch data under.
STEP = "checklist_eval"


PASS_THRESHOLD = 0.80

# v2 cookbook schema : density is measured in
# explanation-words-per-subtopic, NOT chars-per-paragraph.
DENSITY_MIN_AVG_EXPLANATION_WORDS = 12.0
DENSITY_MAX_AVG_EXPLANATION_WORDS = 70.0

REPAIR_RATE_MAX = 0.50
MIN_CITATIONS_PER_SECTION = 1
MAX_RENDERED_CHAPTER_CHARS = 40_000   # 60k -> 40k chars (+ the digest at MAX_RENDERED_DIGEST_CHARS): the judge of the two biggest chapters timed out on 310 s of 504s
MAX_RENDERED_DIGEST_CHARS = 12_000
FEEDBACK_MIN_CHARS = 4
FEEDBACK_MAX_CHARS = 600

# code density gate.
MIN_AVG_CODE_REFS_PER_SECTION = 2.0   # average across sections
MIN_CODE_REF_COVERAGE_FRACTION = 0.5  # fraction of allowed_hashes cited

# Names of the LLM-judge criteria — used both as keys in the LLM JSON
# output AND as identifiers in CriterionResult.name. Adding/removing
# from here requires a prompt_version bump.
LLM_CRITERIA = (
    "chapter_reads_coherently",
    "claims_grounded_in_sources",
    "terminology_consistent",
    "prose_code_first_not_meta_framing",
    "code_refs_introduced_in_prose",
)

BLOB_PREFIX = "synth"




SYNC_ONLY_METHODS = frozenset({
    "strip", "lstrip", "rstrip", "split", "rsplit", "splitlines",
    "join", "replace", "format", "lower", "upper", "title", "casefold",
    "encode", "decode", "startswith", "endswith", "isdigit", "isalpha",
    "isalnum", "isspace", "zfill", "ljust", "rjust", "center",
    "append", "extend", "insert", "pop", "remove", "sort", "reverse",
    "get", "keys", "values", "items", "update", "setdefault",
})


CONTEXT_OVERFLOW_MARKERS = (
    "context_length", "context window", "maximum context length",
    "context_window_exceeded", "reduce the length", "too many tokens",
    "context length exceeded", "prompt is too long",
)

# Judge reasons that carry no actionable feedback (empty or a template echo) —
# the writer has nothing to fix. Lowercase, punctuation-stripped.
PLACEHOLDER_FEEDBACK = {
    "", "some feedback", "feedback", "no feedback", "na", "none",
    "tbd", "todo", "reason",
}


# checklist_eval went silent for 20+ minutes with no trace of where it was
# stuck, coinciding with a real MinIO/network degradation window (Langfuse
# + Alloy exporters were also timing out at the same time). A stuck write
# here should fail loud and bounded, not hang indefinitely and invisibly.
TIMEOUT_S_PERSIST_WRITE = 60.0


# A model call that fails for a transient provider reason is asked again this many times in all, waiting `RETRY_WAIT_S` x attempt between.
ASK_ATTEMPTS = 2
RETRY_WAIT_S = 4.0
# Judge runs before the first cool-off (the first failure also re-renders a lighter chapter).
MAX_CALL_ATTEMPTS = 2
# A judge that stays unreachable after MAX_CALL_ATTEMPTS gets one cool-off and JUDGE_EXTRA_ATTEMPTS more tries before the chapter is
# judged without it (asyncio ch-05: a Rotator outage failed all 5 judge criteria after 177 s and a perfectly gated chapter was rewritten).
JUDGE_COOLOFF_S = 45.0
# Time one judge run is given before the budget below counts it as spent.
JUDGE_RUN_BUDGET_S = 150.0
JUDGE_EXTRA_ATTEMPTS = 2
# …but the judge never waits longer than this in total (attempt time + cool-offs): fastmcp ch-01 spent 400 s on four timed-out attempts and still
# shipped without an LLM review. A retry that cannot finish inside the budget is not started, and the first failure re-renders a lighter prompt.
JUDGE_TOTAL_BUDGET_S = 420.0

# A judge that fails (almost) every LLM criterion on a chapter the gates mostly accept is checked before a rewrite: two more judge runs, per-criterion majority
# (fastmcp ch-01: one judge model failed 5/5 on prose other judges passed 5/5, and the rewrite it triggered cost 15 min and changed nothing).
JUDGE_HARSH_FAILS = 4
JUDGE_SECOND_OPINIONS = 2

# check_code_coverage: a chapter that routes at least COVERAGE_MIN_ROUTED code blocks must print at least COVERAGE_MIN_CODE of
# them, and a chapter of at least COVERAGE_MIN_SOURCE_COUNT sources must cite at least COVERAGE_MIN_SOURCES of them (asyncio
# ch-01 printed 14 of 35 blocks and cited 13 of 23 sources while passing 14/15).
COVERAGE_MIN_ROUTED = 6
COVERAGE_MIN_CODE = 0.60
# ... but never more than this share of what the chapter's sections can print at all (sections x TOPUP_SECTION_MAX subtopics, one block
# each): fastmcp ch-01 routed 133 blocks to 4 sections (64 subtopic slots), so 60% of 133 = 80 was unreachable and every iteration failed.
COVERAGE_CAPACITY_SHARE = 0.5     # 0.75 -> 0.5: langchain ch-04 routed 234 blocks to 11 sections (capacity 176); 99 printed (56%) could never reach 132 and the rewrite it triggered changed nothing
COVERAGE_MIN_SOURCE_COUNT = 6
COVERAGE_MIN_SOURCES = 0.70

# Quality reported to the Rotator for a judge that passed every LLM criterion while one of these deterministic gates failed.
JUDGE_CONTRADICTED_BY = ("code_prose_alignment", "code_coverage", "code_uniqueness_ratio", "code_snippet_sanity")
FEEDBACK_LENIENT_JUDGE_QUALITY = 0.2

