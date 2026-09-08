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
CHECKLIST_PROMPT_VERSION = "v6-cocoa-atomic-claim-disabled-2026-09-07"
