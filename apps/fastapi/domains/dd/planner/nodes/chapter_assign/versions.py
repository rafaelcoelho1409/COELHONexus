"""chapter_assign prompt version — bumped so a re-plan re-runs assign
with the latest prompt/fallback policy active.

v2 (2026-05-30) — lexical fallback assignment on assign-LLM failure
(mirror of doc_distill's fallback): a doc whose scoring call fails is
routed to its best word-overlap chapter at threshold confidence instead
of being dropped from the book.
"""
from __future__ import annotations


PROMPT_VERSION = "v2-assign-fallback-2026-05-30"
