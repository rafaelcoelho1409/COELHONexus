"""Dispatch — tunable scalars."""
from __future__ import annotations


# Per-chapter thread_id format. Matches `_make_thread_id` in api/v1/dd/synth.py
# so JS-pre-generated UUIDs stay compatible.
CHAPTER_THREAD_PREFIX = "docs-distiller/synth"

# Per-study orchestrator thread_id format; distinct from per-chapter so
# pattern-matchers (SQL/Redis) can tell them apart.
STUDY_THREAD_PREFIX = "docs-distiller/study"

# Minimum chapter count required to run the post-study book_harmonize pass.
BOOK_HARMONIZE_MIN_CHAPTERS = 2
