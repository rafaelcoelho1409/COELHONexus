"""Run Ask's Neo4j retrieval specialist."""
from __future__ import annotations
from .. import service
from ....... import agents


async def retrieve(state: dict, context) -> dict:
    results = await service.execute_source(
        "neo4j",
        agents.ask.neo4j_retriever.agent.neo4j_retriever,
        state,
        context,
    )
    return {"neo4j_results": results}
