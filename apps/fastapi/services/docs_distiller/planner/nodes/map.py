"""Substep 5 — map: per-shard LLM labeling.

NO-OP STUB. Real impl: shard the deduped corpus into ≤40-file groups,
fire one LLM call per shard via the rotator (build_llm_fallback_chain
at T=0.0), structured output = {cluster_id, label, file_assignments}.
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("map")
async def map_node(state: PlannerState) -> dict:
    return {"shard_results": []}
