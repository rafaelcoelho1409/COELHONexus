"""Novelty judge — for a RR digest's `arxiv_ids`, score how novel they are
versus prior digests. Pure Jaccard distance; no LLM call.

Returns a float in [0.0, 1.0]:
  1.0 = all arxiv_ids are new (no overlap with prior)
  0.0 = all arxiv_ids appeared in prior digests
  0.5 = half are new

Inputs:
  input_     {"prior_arxiv_ids": [...]}
  expected   ignored (this judge measures freshness, not match-to-expected)
  actual     {"arxiv_ids": [...]}
"""
from __future__ import annotations

import logging


logger = logging.getLogger(__name__)


async def novelty(input_: dict, expected: dict, actual: dict) -> float:
    """1.0 − Jaccard(actual ∩ prior, actual)."""
    prior = set(input_.get("prior_arxiv_ids") or [])
    current = set(actual.get("arxiv_ids") or [])
    if not current:
        return 0.0
    overlap = prior & current
    novelty_ratio = 1.0 - (len(overlap) / len(current))
    return float(round(novelty_ratio, 4))
