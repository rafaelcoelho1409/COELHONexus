"""corpus_load — pure helpers (percentile + stats builder)."""
from __future__ import annotations


def percentile(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile on a pre-sorted list. p ∈ [0, 100]."""
    if not sorted_values:
        return 0
    n = len(sorted_values)
    idx = min(n - 1, max(0, int(round(p / 100.0 * (n - 1)))))
    return sorted_values[idx]


def build_corpus_stats(
    byte_sizes: list[int],
    manifest: dict,
    load_ms: int,
    excluded_changelog: int = 0,
    excluded_course: int = 0,
    excluded_blog: int = 0,
) -> dict:
    """Compute the corpus stats dict from a sorted list of per-page byte
    sizes. Mirrors the v1 PlannerProgress `record_corpus_load()` fields."""
    byte_sizes = sorted(byte_sizes)
    n = len(byte_sizes)
    total_bytes = sum(byte_sizes)
    return {
        "total_files":  n,
        "total_bytes":  total_bytes,
        "min_bytes":    byte_sizes[0]  if n else 0,
        "max_bytes":    byte_sizes[-1] if n else 0,
        "p10_bytes":    percentile(byte_sizes, 10),
        "median_bytes": percentile(byte_sizes, 50),
        "p90_bytes":    percentile(byte_sizes, 90),
        "load_ms":      load_ms,
        "tier_kind":    manifest.get("tier_kind"),
        "ingested_at":  manifest.get("ingested_at"),
        "excluded_changelog": excluded_changelog,
        "excluded_course": excluded_course,
        "excluded_blog": excluded_blog,
        # Tier 2 llms.txt blockquote summary ("", absent for all other tiers).
        # off_topic reads this for its judge-prompt anchor; "" = static anchor.
        "index_summary": manifest.get("index_summary") or "",
    }
