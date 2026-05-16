"""Substep 4 — cache_lookup: hash the deduped manifest, look up in Redis.

NO-OP STUB. Real impl: hash(deduped_files) → Redis key; on hit, populate
state["cached_plan"] AND route directly to plan_write via conditional
edge. On miss, leave cached_plan=None and continue to map.
"""
from __future__ import annotations

from ..observability.spans import traced
from ..state import PlannerState


@traced("cache_lookup")
async def cache_lookup(state: PlannerState) -> dict:
    # Stub: always miss. The conditional edge in graph.py will route
    # `None → map`; once cached_plan is populated by the real impl,
    # routing flips to plan_write.
    return {"cached_plan": None}
