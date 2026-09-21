"""Thread-id key builders — the `docs-distiller/<kind>/<slug>/<uuid>` shape.

The prefixes are a cross-boundary contract, not tunables: JS-side
pre-generation (`_make_thread_id` in api/v1/dd/synth.py) and SQL/Redis
pattern-matchers both depend on the exact strings, so prefix and
builder live together here — one place to keep them in agreement.
"""
from __future__ import annotations

import uuid


# Per-chapter thread_id format. Matches `_make_thread_id` in api/v1/dd/synth.py
# so JS-pre-generated UUIDs stay compatible.
CHAPTER_THREAD_PREFIX = "docs-distiller/synth"

# Per-study orchestrator thread_id format; distinct from per-chapter so
# pattern-matchers (SQL/Redis) can tell them apart.
STUDY_THREAD_PREFIX = "docs-distiller/study"


def make_thread_id(slug: str) -> str:
    """Per-chapter thread_id; JS-side pre-generation uses the same format."""
    return f"{CHAPTER_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"


def make_study_thread_id(slug: str) -> str:
    """Per-study thread_id with distinct prefix from per-chapter for Redis/SQL pattern matching."""
    return f"{STUDY_THREAD_PREFIX}/{slug}/{uuid.uuid4()}"
