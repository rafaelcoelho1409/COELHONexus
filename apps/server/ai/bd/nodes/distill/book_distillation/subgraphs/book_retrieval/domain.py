"""Pure retrieval evidence fusion for the Distill book retrieval subgraph."""
from __future__ import annotations
from . import params

from typing import Any


def fuse_ranked_evidence(ranked_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge rankings without comparing incomparable raw backend scores."""
    fused: dict[str, dict[str, Any]] = {}
    for list_index, ranked in enumerate(ranked_lists):
        for rank, item in enumerate(ranked, start = 1):
            text = " ".join(str(item.get("text") or "").split())
            fingerprint = text.casefold()
            if not fingerprint:
                continue
            reciprocal_rank = 1.0 / (params.RRF_K + rank)
            matched_sources = {str(item.get("source") or "unknown")}
            prior = fused.get(fingerprint)
            if prior is None:
                fused[fingerprint] = {
                    "item": dict(item),
                    "score": reciprocal_rank,
                    "best_rank": (rank, list_index),
                    "matched_sources": matched_sources,
                }
                continue
            prior["score"] += reciprocal_rank
            prior["matched_sources"].update(matched_sources)
            if (rank, list_index) < prior["best_rank"]:
                prior["item"] = dict(item)
                prior["best_rank"] = (rank, list_index)

    ordered = sorted(
        fused.items(),
        key = lambda pair: (-pair[1]["score"], pair[0]),
    )[:params.MAX_MERGED_EVIDENCE]
    evidence = []
    for _, value in ordered:
        item = value["item"]
        item["backend_score"] = item.get("score", 0.0)
        item["score"] = value["score"]
        item["matched_sources"] = sorted(value["matched_sources"])
        evidence.append(item)
    return evidence
