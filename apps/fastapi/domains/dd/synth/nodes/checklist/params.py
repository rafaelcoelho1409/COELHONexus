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
MAX_RENDERED_CHAPTER_CHARS = 60_000
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
