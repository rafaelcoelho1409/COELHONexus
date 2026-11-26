"""Run Ask's Qdrant retrieval specialist."""
from __future__ import annotations
from .. import service
from ....... import agents

async def retrieve(state: dict, context) -> dict:
    results = await service.execute_source(
        "qdrant",
        agents.ask.qdrant_retriever.agent.qdrant_retriever,
        state,
        context,
    )
    return {"qdrant_results": results}
