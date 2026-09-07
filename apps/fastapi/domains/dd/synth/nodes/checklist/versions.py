"""checklist_eval — bump when prompt or schema changes."""
from __future__ import annotations


CHECKLIST_SCHEMA_VERSION = "1.0"
# v5: faithfulness.py / cocoa.py's evaluated-fraction floor now also
# requires an absolute minimum failure/gap count (issue #14, 2026-09-06)
# before treating a low fraction as an infra signal — a single incidental
# call failure on a small-claim chapter no longer flips infra_degraded.
CHECKLIST_PROMPT_VERSION = "v5-small-sample-infra-floor-2026-09-06"
