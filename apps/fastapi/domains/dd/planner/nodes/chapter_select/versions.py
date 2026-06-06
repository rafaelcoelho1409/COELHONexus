"""chapter_select cache version — no LLM, but bumped to invalidate cache
when the selection policy (greedy thresholds / pruning rules) changes.

v2 (2026-06-05) — orphan-protection in the prune step. A proposed
chapter that's below MIN_DOCS_PER_CHAPTER is no longer pruned when
any of its member docs has no above-threshold score in any OTHER
selected chapter. Without this, a small-but-essential chapter (e.g.
"Skills and Custom Commands" on the Claude Code corpus) would be
dropped and its docs orphaned — even though every doc landed there
because it had nowhere else to go. Companion to chapter_assign v3's
rescue pass (memory `project_planner_cc_coverage_2026_05_29`)."""
from __future__ import annotations


PROMPT_VERSION = "v2-no-orphan-prune-2026-06-05"
