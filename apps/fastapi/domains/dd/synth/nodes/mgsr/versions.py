"""mgsr — schema + prompt cache-invalidation markers."""
from __future__ import annotations


MGSR_SCHEMA_VERSION = "1.0"
# v3 (issue #19, 2026-09-07): the real LLM replan call is no longer fired
# on the non-trivial-pass path — its `actions`/rationale output had zero
# downstream consumers (confirmed: `_route_after_mgsr` only reads
# checklist_stats), so every non-trivial iteration always resolves via
# the existing `fallback_decision` path instead of a real ~90s-per-
# attempt LLM call. Bumped to invalidate any cached mgsr blob computed
# under the old (real-call) logic.
MGSR_PROMPT_VERSION = "v3-replan-call-skipped-2026-09-07"
