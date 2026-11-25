from __future__ import annotations
from ... import service
from ..... import graphs

from langgraph.runtime import Runtime


async def retrieve(state: graphs.rag.retrieval.states.RetrievalGraphState, runtime: Runtime[graphs.rag.retrieval.entities.RetrievalContext]) -> dict:
    return await service.retrieve_source("qdrant", state, runtime)
