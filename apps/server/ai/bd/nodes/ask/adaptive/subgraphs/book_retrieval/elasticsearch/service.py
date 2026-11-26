"""Run Ask's Elasticsearch retrieval specialist."""
from __future__ import annotations
from .. import service
from ....... import agents


async def retrieve(state: dict, context) -> dict:
    results = await service.execute_source(
        "elasticsearch",
        agents.ask.elasticsearch_retriever.agent.elasticsearch_retriever,
        state,
        context,
    )
    return {"elasticsearch_results": results}
