"""Fuse source results and persist retrieval context for later Distill stages."""
from __future__ import annotations
import domains
from .. import domain

from typing import Any


async def fuse_evidence(state: dict[str, Any], dependencies: Any) -> dict[str, Any]:
    results = {
        source: state.get(f"{source}_result") or {"status": "unavailable", "evidence": [], "queries": []}
        for source in domains.bd.distill.ai.params.RETRIEVAL_SOURCE_NODES
    }
    ranked_lists = [
        result.get("evidence") or []
        for result in results.values()
        if result.get("status") != "not_requested"
    ]
    fused = {
        "evidence": domain.fuse_ranked_evidence(ranked_lists),
        "source_status": {
            source: str(result.get("status") or "unavailable")
            for source, result in results.items()
        },
        "source_queries": {
            source: [str(value)[:1000] for value in result.get("queries", [])[:2]]
            for source, result in results.items()
        },
    }
    if hasattr(dependencies, "storage"):
        await domains.bd.distill.ai.retrieval_context.service.persist_retrieval_context(
            dependencies.storage,
            dependencies.book_id,
            dependencies.run_id,
            state,
            fused,
        )
    return fused
