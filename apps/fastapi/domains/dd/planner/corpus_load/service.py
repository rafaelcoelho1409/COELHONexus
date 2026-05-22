"""corpus_load helpers — percentile utility."""
from __future__ import annotations


def _percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. p ∈ [0, 100]."""
    if not sorted_values:
        return 0
    n = len(sorted_values)
    idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
    return sorted_values[idx]
