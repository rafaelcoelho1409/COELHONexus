"""doc_distill prompt/cache version — bumped when the prompt or fallback
policy changes so a re-plan re-distills under the new shape.

v2 (2026-05-30) — fallback distillate on LLM-distill failure (Fix #4):
a doc with content but a failed distill is no longer silently dropped
from the book; it gets a deterministic title/identifier-derived
distillate so it flows through chapter_assign + chapter_select.

v3 (2026-06-05) — transient-error retry + per-doc failure-reason
classification. Memory `project_planner_cc_coverage_2026_05_29` flagged
17 silent failures (rate-limit / timeout) with no retry; the fallback
fired immediately. v3 retries up to MAX_TRANSIENT_RETRIES on rate_limit
and timeout (bandit rotates to a different deployment on retry), and
surfaces a per-doc reason string in the persisted blob so the operator
can tell rate-limit pressure from a genuine parse / context-length fail.
"""
from __future__ import annotations


PROMPT_VERSION = "v3-retry-classify-2026-06-05"
