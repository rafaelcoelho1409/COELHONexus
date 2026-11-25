"""Deterministically merge specialist evidence and preserve source status."""
from __future__ import annotations
import domains
from .. import keys

from typing import Any


def fuse_evidence(state: dict[str, Any]) -> dict[str, Any]:
    results = {
        source: state.get(key) or {"status": "unavailable", "evidence": [], "queries": []}
        for source, key in keys.RETRIEVAL_RESULT_KEY_BY_SOURCE.items()
    }
    ranked_lists = [
        result.get("evidence") or []
        for source, result in results.items()
        if result.get("status") != "not_requested"
    ]
    evidence = domains.bd.rag.ai.retrieval.service.fuse_evidence(ranked_lists)
    return {
        "evidence": evidence,
        "source_status": {source: str(result.get("status") or "unavailable") for source, result in results.items()},
        "source_queries": {
            source: [str(value)[:1000] for value in result.get("queries", [])[:2]]
            for source, result in results.items()
        },
    }
