"""Run the web-search source specialist for Distill."""
from __future__ import annotations

import domains

from ...... import agents, graphs
from .. import service as retrieval_service

from typing import Any

SOURCE_NAME = "web_search"


async def retrieve(
    state: graphs.distill.states.DistillRunState,
    dependencies: graphs.distill.entities.DistillDependencies,
) -> dict[str, Any]:
    """Run the web-search agent with Distill's retrieval context."""
    context = domains.bd.distill.ai.retrieval_context.domain.source_context(
        book_id = dependencies.book_id,
        ingestion_ai_run_id = dependencies.ingestion_ai_run_id,
        counter_run_id = dependencies.run_id,
    )
    return await retrieval_service.execute_retrieval_agent(
        SOURCE_NAME,
        agents.distill.web_search_retriever.agent.web_search_retriever,
        state,
        context,
    )
