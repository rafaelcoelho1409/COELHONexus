"""LangGraph node for Ask's Neo4j retrieval specialist."""
from __future__ import annotations
from . import service
from ....... import graphs

from langgraph.runtime import Runtime


async def retrieve(
    state: graphs.ask.adaptive.subgraphs.book_retrieval.states.BookRetrievalState,
    runtime: Runtime[graphs.ask.adaptive.entities.AskContext],
) -> dict:
    return await graphs.service.run_node(
        graphs.service.stream_progress_callback(),
        "neo4j",
        lambda: service.retrieve(state, runtime.context),
    )
