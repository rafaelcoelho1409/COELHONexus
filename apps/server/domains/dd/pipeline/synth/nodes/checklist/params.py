"""checklist_eval — tunable thresholds + LLM-judge criterion names."""
from __future__ import annotations


PASS_THRESHOLD = 0.80

# v1 schema (paragraph-mode) — kept for backwards-compat with any legacy
# blobs still emitting avg_chars_per_paragraph in coverage_stats.
DENSITY_MIN_CHARS_PER_PARA = 150
DENSITY_MAX_CHARS_PER_PARA = 1200
# v2 cookbook schema : density is measured in
# explanation-words-per-subtopic, NOT chars-per-paragraph.
DENSITY_MIN_AVG_EXPLANATION_WORDS = 12.0
DENSITY_MAX_AVG_EXPLANATION_WORDS = 70.0

REPAIR_RATE_MAX = 0.50
PICKER_FALLBACK_RATE_MAX = 0.50
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


# CoCoA two-stage alignment check (arXiv 2410.03131) — overrides c11+c12 on drift.
# Issue #20, 2026-09-07: temporarily disabled. Confirmed timing out on
# every single call observed across all three measured post-#17 runs
# (ch-01 x2, ch-02) — under current Rotator conditions this check is
# paying its full 90-120s cost on every checklist_eval iteration without
# completing even once. Fail-soft by design (can only ever downgrade the
# bundled judge's own verdict, never upgrade it), so disabling loses a
# currently-dormant safety net, not a working one — re-enable by
# flipping this back to True once a spot-check shows a real completion
# rate (see SYNTH-PERFORMANCE-ANALYSIS-2026-09-07.md).
COCOA_ENABLED = False

COCOA_MAX_SUBTOPICS_PER_BATCH = 60

COCOA_EXPLAINER_MAX_TOKENS = 8000
COCOA_JUDGE_MAX_TOKENS     = 4000
COCOA_EXPLAINER_TEMPERATURE = 0.0
COCOA_JUDGE_TEMPERATURE     = 0.0
# chat_text_async's own default (30s) was undersized — same fix
# as elsewhere in Synth (2026-09-06/07). A failed CoCoA call here feeds
# `infra_degraded` (issues #10/#14) the same way the bundled judge does.
COCOA_EXPLAINER_TIMEOUT_S  = 120.0
COCOA_JUDGE_TIMEOUT_S      = 90.0

# reverted 0.70 → 0.85 (CC run: 0.70 let through catastrophic mismatches; keyword-overlap pre-check now covers the BU regression).
COCOA_ALIGN_PASS_FRACTION = 0.85
# Below this fraction of LLM-stage pairs actually judged (explainer/judge
# call failures dropped the rest silently before this fix), the alignment
# rate is noise, not signal — an infra outage must not read as prose drift.
COCOA_MIN_EVALUATED_FRACTION = 0.5
# Same small-sample correction as the atomic-claim-grounding twin constant
# (issue #14, 2026-09-06): with only 1-2 code pairs (small chapters), a
# single incidental explainer/judge call failure already reads as 0%
# evaluated. Require this many actual missing verdicts before trusting
# the fraction.
COCOA_MIN_ABSOLUTE_GAP_FOR_UNRESOLVED = 2


# Atomic-claim grounding — augments bundled LLM-judge's `claims_grounded_in_sources` via conservative-bias merge.
# Issue #20, 2026-09-07: temporarily disabled. Confirmed timing out on
# every single call observed across all three measured post-#17 runs
# (ch-01 x2, ch-02) — under current Rotator conditions this check is
# paying its full 45-60s cost on every checklist_eval iteration without
# completing even once. Fail-soft by design (can only ever downgrade the
# bundled judge's own verdict, never upgrade it), so disabling loses a
# currently-dormant safety net, not a working one — re-enable by
# flipping this back to True once a spot-check shows a real completion
# rate (see SYNTH-PERFORMANCE-ANALYSIS-2026-09-07.md).
ATOMIC_CLAIM_ENABLED = False

ATOMIC_CLAIM_CONCURRENCY = 8
ATOMIC_CLAIM_MAX_CLAIMS = 30
ATOMIC_CLAIM_PROSE_CHARS = 12000
ATOMIC_CLAIM_SOURCE_CHARS = 12000
ATOMIC_CLAIM_EXTRACT_MAX_TOKENS = 1500
ATOMIC_CLAIM_JUDGE_MAX_TOKENS = 200
# chat_text_async's own default (30s) was undersized — same fix
# as elsewhere in Synth (2026-09-06/07). Also directly relevant to issue
# #14: a call timeout here is a genuine judge-call failure and correctly
# feeds infra_degraded — but a timeout that would've succeeded with more
# headroom is a false positive, not a real signal.
ATOMIC_CLAIM_EXTRACT_TIMEOUT_S = 60.0
ATOMIC_CLAIM_JUDGE_TIMEOUT_S = 45.0
ATOMIC_CLAIM_MIN_CLAIMS_FOR_RUN = 1
# raised 0.60 → 0.75 (Run 5: judge flags code-demonstrated claims as unsupported when source TEXT doesn't restate; 0.75 still catches catastrophic hallucination ≥85%).
ATOMIC_CLAIM_MAX_UNSUPPORTED_RATIO = 0.75
# Below this fraction of claims actually judged (vs. fail-soft defaults from
# a broken call), the ratio above is noise, not signal — extraction/judge
# outages must not silently read as "verified faithful."
ATOMIC_CLAIM_MIN_EVALUATED_FRACTION = 0.5
# A fraction is only a meaningful signal once it's measured over enough
# trials — with 1-2 claims (small chapters), a single incidental judge-call
# blip already reads as 0% evaluated, indistinguishable from a real outage.
# Same "≥2, not 1" bar this codebase already uses for SUSTAINED_INFRA_
# OUTAGE_LIMIT: require at least this many actual call failures before the
# fraction floor above is trusted (issue #14, 2026-09-06 — confirmed live:
# a 1-section chapter's single timeout was folded into infra_degraded and
# fed the sustained-outage counter).
ATOMIC_CLAIM_MIN_ABSOLUTE_FAILURES_FOR_UNRESOLVED = 2


# Common words that don't carry alignment signal even if they appear in
# code (control-flow keywords, generic verbs). Lowercase. Frozenset for
# O(1) membership.
NOISE_IDENTS = frozenset({
    "for", "and", "the", "with", "from", "import", "return", "true",
    "false", "none", "null", "this", "self", "type", "string", "int",
    "bool", "list", "dict", "set", "tuple", "any", "all", "function",
    "async", "await", "class", "def", "let", "var", "const", "new",
    "try", "except", "finally", "throw", "throws", "while", "case",
    "switch", "break", "continue", "yield", "lambda", "print", "log",
    "console", "data", "value", "result", "options", "params", "args",
    "main", "init", "name", "key", "id", "config", "test", "tests",
    "example", "examples", "default", "true", "false",
})
MIN_IDENT_LEN = 3

CODE_EXCERPT_CHARS = 1200


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


# Draft-call attempts before falling back to the conservative all-FAIL
# verdict (which triggers a full mgsr_replan cycle) — same idiom as
# outline_sdp/digest_construct/sawc_write's context-overflow retry.
MAX_CALL_ATTEMPTS = 2
# The bundled judge and the repair call that follows it.
JUDGE_TEMPERATURE  = 0.0
REPAIR_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS   = 3000
REPAIR_MAX_TOKENS  = 3000
# chat_text_async's own default (30s) was undersized — same fix
# as outline/digest/sawc (2026-09-06/07). A failed bundled-judge call
# here directly feeds `infra_degraded` (issues #10/#14), so a timeout
# that would have succeeded with more headroom was actively corrupting
# the sustained-outage signal, not just costing one bad iteration.
JUDGE_TIMEOUT_S = 150.0   # was 90 s: the Rotator's cascade wall is timeout - 3 s, which left room for ~2 arm tries when NIM arms stall at 36 s (fastmcp ch-01 and langfuse ch-11 judges, 87.5 s 504s)
REPAIR_TIMEOUT_S = 90.0
MAX_REPAIR_ATTEMPTS = 1
# A judge that stays unreachable after MAX_CALL_ATTEMPTS gets one cool-off and JUDGE_EXTRA_ATTEMPTS more tries before the chapter is
# judged without it (asyncio ch-05: a Rotator outage failed all 5 judge criteria after 177 s and a perfectly gated chapter was rewritten).
JUDGE_COOLOFF_S = 45.0
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

# The batched LLM judge asks the Rotator for its own bandit cell (see FEEDBACK_LENIENT_JUDGE_QUALITY above).
ROTATOR_TASK = "dd.judge"

