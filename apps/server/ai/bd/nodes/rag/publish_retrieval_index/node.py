"""LangGraph adapter for publishing the completed store manifest."""
from __future__ import annotations
import domains
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def publish_retrieval_index(
    state: graphs.rag.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.rag.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context
    return await graphs.rag.service.run_index_node(
        context,
        "publish_retrieval_index",
        lambda: domains.bd.rag.ai.publication.service.publish_index(
            state,
            context.storage,
        ),
    )
