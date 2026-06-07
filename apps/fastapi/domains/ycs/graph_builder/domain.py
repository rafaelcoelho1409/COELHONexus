"""ycs/graph_builder — PURE entity-resolution helpers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no Neo4j, no LLM, no
rapidfuzz import. The fuzzy-ratio comparison itself stays in `service.py`
because rapidfuzz IS a library call, but the *decisions* it feeds into
(canonical-name selection, label-skip filter, embedding-cosine merge
gate) live here so they're trivially testable."""
from __future__ import annotations

import math
from typing import Sequence

from .params import EMBED_COSINE_CUTOFF, NUMERIC_LABELS_SKIP


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


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length real-valued vectors.

    Pure stdlib math — no numpy import to keep `domain.py` zero-dep.
    Returns 0.0 if either vector has zero magnitude (degenerate case;
    safer than raising and forcing the caller to handle it). The
    inputs are typically already L2-normalized at the API boundary,
    but we don't assume it — explicit normalization makes the function
    self-contained for tests."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def should_merge_by_cosine(
    cosine: float, cutoff: float = EMBED_COSINE_CUTOFF,
) -> bool:
    """Semantic merge gate — pass iff the cosine clears the empirical
    `EMBED_COSINE_CUTOFF` (default 0.85). Pulled out as a named decision
    so the threshold lives in `params.py` and the call site stays
    readable."""
    return cosine >= cutoff
