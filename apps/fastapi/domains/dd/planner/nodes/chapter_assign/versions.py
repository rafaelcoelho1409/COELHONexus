"""chapter_assign prompt version — bumped so a re-plan re-runs assign
with the latest prompt/fallback policy active.

v2 (2026-05-30) — lexical fallback assignment on assign-LLM failure
(mirror of doc_distill's fallback): a doc whose scoring call fails is
routed to its best word-overlap chapter at threshold confidence instead
of being dropped from the book.

v3 (2026-06-05) — RESCUE pass for sub-threshold-but-above-floor docs.
Memory `project_planner_cc_coverage_2026_05_29` documented ~18 genuine
content docs lost on the Claude Code corpus because their LLM
confidence sat in the [0.3, 0.5) band — high enough that the judge was
clearly identifying a topical home, low enough that greedy_select's
`assignable` filter (>=0.5) excluded them from coverage. v3 floors the
best score for any such doc up to CONFIDENCE_THRESHOLD so it reaches a
chapter; docs with max < RESCUE_FLOOR are left alone (LLM says "not a
fit anywhere"). Persisted blob carries a `rescued` list so the lineage
is auditable.
"""
from __future__ import annotations


PROMPT_VERSION = "v3-rescue-pass-2026-06-05"
