"""Pure functions for the RR agent's triage tool — no I/O, no event loop.

The deterministic Phase-2 scoring/dedup/diversity pipeline from the
architecture doc:

  normalize → dedup_by_arxiv_id → diversify by source → signal_score
  → top-N
"""
from __future__ import annotations

import logging
from typing import Any

from .... import domain as rr_domain, entities as rr_entities, keys as rr_keys
from . import params


logger = logging.getLogger(__name__)


# Source → normalizer mapping. Looked up at runtime so we can stay tolerant
# to a missing discovery output (e.g. the hn subagent crashed mid-scan).
NORMALIZER_BY_SOURCE = {
    rr_keys.SOURCE_ARXIV:    rr_domain.normalize_arxiv,
    rr_keys.SOURCE_S2:       rr_domain.normalize_s2,
    rr_keys.SOURCE_HF:       rr_domain.normalize_hf,
    rr_keys.SOURCE_HN:       rr_domain.normalize_hn,
    rr_keys.SOURCE_OPENALEX: rr_domain.normalize_openalex,
}


def diversify_by_source(
    scored: list[tuple[Any, float]],
    top_n: int,
    per_source_counts: dict[str, int],
) -> list[tuple[Any, float]]:
    """Force min-1-per-source in the top-N when multiple sources have
    real content. Picks the highest-scored candidate from each qualifying
    source first, then fills remaining slots in pure score order. Falls
    back to score-only when only one source qualifies."""
    qualifying_sources = {
        src for src, n in per_source_counts.items() if n >= params.MIN_PER_SOURCE_FLOOR
    }
    if len(qualifying_sources) < 2:
        return scored[:top_n]
    # Score-order is preserved within each source's candidate list.
    by_source: dict[str, list[tuple[Any, float]]] = {
        s: [] for s in qualifying_sources
    }
    leftovers: list[tuple[Any, float]] = []
    for p, s in scored:
        # Pick the source the candidate belongs to. dedup-merged papers
        # carry the union; we use the first qualifying source for the quota.
        chosen_src: str | None = None
        for src in p.sources:
            if src in qualifying_sources:
                chosen_src = src
                break
        if chosen_src is not None:
            by_source[chosen_src].append((p, s))
        else:
            leftovers.append((p, s))
    out: list[tuple[Any, float]] = []
    seen_ids: set[str] = set()
    for src in sorted(by_source.keys()):
        bucket = by_source[src]
        if not bucket:
            continue
        p, s = bucket[0]
        if p.arxiv_id and p.arxiv_id in seen_ids:
            continue
        out.append((p, s))
        if p.arxiv_id:
            seen_ids.add(p.arxiv_id)
        if len(out) >= top_n:
            return out
    remaining_pool = [
        (p, s) for p, s in scored
        if not p.arxiv_id or p.arxiv_id not in seen_ids
    ]
    for p, s in remaining_pool:
        if len(out) >= top_n:
            break
        if p.arxiv_id and p.arxiv_id in seen_ids:
            continue
        out.append((p, s))
        if p.arxiv_id:
            seen_ids.add(p.arxiv_id)
    logger.info(
        f"[triage] diversity quota applied: {len(qualifying_sources)} qualifying sources "
        f"(min_floor={params.MIN_PER_SOURCE_FLOOR}); top_n={len(out)} composed"
    )
    return out


def paper_as_dict(
    p: rr_entities.NormalizedPaper, *, score: float, topical_logit: float | None = None,
) -> dict[str, Any]:
    """Materialize a NormalizedPaper as a JSON-safe dict for fs storage."""
    return {
        "arxiv_id":              p.arxiv_id,
        "title":                 p.title,
        "abstract":              p.abstract,
        "published":             p.published.isoformat() if p.published else None,
        "authors":               list(p.authors),
        "categories":            list(p.categories),
        "citations":             p.citations,
        "influential_citations": p.influential_citations,
        "hn_points":             p.hn_points,
        "hn_num_comments":       p.hn_num_comments,
        "hf_upvotes":            p.hf_upvotes,
        "sources":               sorted(p.sources),
        "signal":                float(score),
        "topical_logit":         (None if topical_logit is None else float(topical_logit)),
    }
