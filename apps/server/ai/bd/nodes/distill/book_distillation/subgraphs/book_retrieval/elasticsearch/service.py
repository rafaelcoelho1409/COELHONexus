"""Run the Elasticsearch source specialist for Distill."""
from __future__ import annotations
import domains
from .. import service
from ....... import agents, graphs

from typing import Any


async def retrieve(
    state: graphs.distill.book_distillation.states.DistillRunState,
    dependencies: graphs.distill.book_distillation.entities.DistillDependencies,
) -> dict[str, Any]:
    """Run the Elasticsearch agent with Distill's retrieval context."""
    context = domains.bd.distill.ai.retrieval_context.domain.source_context(
        book_id = dependencies.book_id,
        ingestion_ai_run_id = dependencies.ingestion_ai_run_id,
        counter_run_id = dependencies.run_id,
    )
    return await service.execute_retrieval_agent(
        "elasticsearch",
        agents.distill.elasticsearch_retriever.agent.elasticsearch_retriever,
        state,
        context,
    )
