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
CHECKLIST_PROMPT_VERSION = "v8-repair-timeout-deployment-reset-2026-09-08"
