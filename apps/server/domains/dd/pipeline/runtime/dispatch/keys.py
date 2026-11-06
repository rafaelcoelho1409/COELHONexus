"""Planner dispatch keys — the thread_id prefix.

The `docs-distiller/{slug}/{uuid}` format is a cross-boundary contract (the web pre-generates ids so the Cancel button
works from click 1), so the prefix lives here, next to its builder in `domain.make_thread_id`.
"""
from __future__ import annotations


THREAD_PREFIX = "docs-distiller"

# Synth thread ids: `docs-distiller/synth/{slug}/{uuid}` is one chapter's run, `docs-distiller/study/{slug}/{uuid}` a whole study's. Distinct prefixes so SQL and
# Redis pattern matchers can tell them apart (the web pre-generates the chapter ids in the same shape).
CHAPTER_THREAD_PREFIX = "docs-distiller/synth"
STUDY_THREAD_PREFIX = "docs-distiller/study"
