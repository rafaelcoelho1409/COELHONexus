"""checklist_eval — bump when prompt or schema changes."""
from __future__ import annotations


CHECKLIST_SCHEMA_VERSION = "1.0"
# v5: faithfulness.py / cocoa.py's evaluated-fraction floor now also
# requires an absolute minimum failure/gap count (issue #14, 2026-09-06)
# before treating a low fraction as an infra signal — a single incidental
# call failure on a small-claim chapter no longer flips infra_degraded.
# v6 (issue #20, 2026-09-07): CoCoA + atomic-claim grounding temporarily
# disabled (COCOA_ENABLED / ATOMIC_CLAIM_ENABLED = False) — both were
# timing out on every call observed across 3 measured runs, paying their
# full cost for a check that wasn't completing. Both fail-soft to a
# genuine resolved=True pass while disabled, so no chapter is penalized;
# re-enable once Rotator reliability is spot-checked.
# v7 (issue #20 follow-up + #22, 2026-09-08 — confirmed live on the ch-05
# retest): _run_cocoa()'s MinIO vault-load now actually skips when
# COCOA_ENABLED=False (previously ran unconditionally before the flag was
# ever checked, and crashed with a ReadTimeoutError mid-run). Also added a
# bounded timeout + before/after log lines around the two post-judge
# result-persistence writes, which had neither and are the likely site of
# a 20+-minute silent freeze during that same retest.
# v8 (2026-09-08 — confirmed live on the same ch-05 retest): `deployment`
# was left holding the main call's value when only the REPAIR call timed
# out, so `judge_call_failed`/`infra_degraded` came out wrongly False for
# that failure shape — a pure infra failure got recorded as a genuine
# content judgment. Now reset to None in that except block, matching the
# main-call-failure path a few lines above it.
# v9 (2026-10-15): two more deterministic gates (code_coverage, code_prose_alignment); best-seen prefers an iteration whose
# deterministic gates all pass.
# v10 (2026-10-15): code_density_appropriate scales its per-section floor to the routed blocks the chapter has (asyncio ch-04 / ch-09:
# 4 and 1 blocks, 1.5 / 0.33 per section against a fixed 2.0).
# v11 (2026-10-15): a judge outage (all 5 LLM criteria "judge_unavailable") no longer sends a gated chapter to RETHINK: the judge gets a cool-off
# and two more tries, then the chapter is scored on its deterministic gates; the picker-fallback ceiling is inclusive; a FAIL with no reason
# (empty or "some feedback") is asked again and, if still unexplained, counted as passed.
# v12 (2026-10-16): check_code_coverage needs at most 75% of what the sections can print (sections x 16 subtopic slots); fastmcp ch-01: 133 blocks -> 4 sections made the 60% bar unreachable.
# v13 (2026-10-16): the judge payload accepts verdicts a model double-encoded as JSON strings (asyncio ch-04: 5 validation errors, judge unavailable, the chapter shipped on the deterministic gates alone).
# v14 (2026-10-16): the LLM judge waits at most JUDGE_TOTAL_BUDGET_S (330 s) in total and re-renders a lighter prompt after its first failed attempt.
# v15 (2026-10-17): the code_coverage gate counts a routed block repeated by a printed near-copy as covered, and a judge that fails >= 4 of 5 LLM criteria is
# checked by two more runs (per-criterion majority) before the chapter is sent back to the writer.
# v16 (2026-10-18): the judge also gets a second opinion whenever its verdict alone would send the chapter back to the writer; the coverage bar is 50% (not 75%) of
# section capacity; the judge reads at most 40k chars of chapter + 12k of digest (the two biggest chapters lost the judge to 310 s of 504s).
# v17 (2026-10-18): judge calls get 150 s (was 90 s) and the judge's total budget is 420 s (was 330 s): the Rotator cascade wall follows the call's timeout, and 87 s fitted
# about two arm attempts when NIM arms stall at 36 s (the two biggest chapters lost the judge three times in a row).
# v18 (2026-10-18): a cached verdict scored without the LLM judge (judge unavailable) is not reused: the chapter is judged again.
CHECKLIST_PROMPT_VERSION = "v18-rejudge-unavailable-2026-10-18"

# CoCoA two-stage alignment check + atomic-claim grounding — independently
# versioned from CHECKLIST_PROMPT_VERSION (own MinIO cache keys, see keys.py).
COCOA_PROMPT_VERSION = "v1-cocoa-2026-05-25"
# Per-prose-hash cache: same prose + fixed prompt → same claims; re-runs hit MinIO instead of LLM.
ATOMIC_CLAIM_PROMPT_VERSION = "v3-cache-2026-05-28"
