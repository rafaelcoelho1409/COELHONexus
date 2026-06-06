"""ycs/graph_builder — PURE entity-resolution helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no Neo4j, no LLM, no
rapidfuzz import. The fuzzy-ratio comparison itself stays in `service.py`
because rapidfuzz IS a library call, but the *decisions* it feeds into
(canonical-name selection, label-skip filter) live here so they're
trivially testable."""
from __future__ import annotations

from .params import NUMERIC_LABELS_SKIP


def pick_canonical(name_a: str, name_b: str) -> tuple[str, str]:
    """Given two near-duplicate entity ids, return `(canonical, duplicate)`.

    Heuristic from deprecated `services/youtube/graph_builder.py:L247-248`:
    the longer name wins ("Saint Kitts and Nevis" beats "St Kitts"). Tie
    goes to `name_a` for stable ordering."""
    if len(name_a) >= len(name_b):
        return name_a, name_b
    return name_b, name_a


def should_skip_fuzzy_label(label: str) -> bool:
    """True for labels whose IDs are numerically- or temporally-distinct
    even when lexically similar (e.g. "$100,000" vs "$1,000,000").

    Mirror of deprecated `SKIP_FUZZY_LABELS` membership check
    (`graph_builder.py:L230-231`)."""
    return label in NUMERIC_LABELS_SKIP
