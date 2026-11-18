"""LangGraph adapter for dense and sparse indexing of processed pages."""
from __future__ import annotations
import domains
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def index_pages(
    state: graphs.rag.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.rag.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context

    async def operation() -> dict[str, Any]:
        rows = await domains.bd.ingestion.ai.retrieval_index.service.read_sources(state, context.storage)
        index_pages = domains.bd.ingestion.ai.retrieval_index.qdrant_index.service.index_pages

        async def report_pages(completed: int, _total: int) -> None:
            await context.progress_callback(
                "qdrant",
                "running",
                {"qdrant_pages_completed": completed},
            )

        collection, model, dimensions, count = await index_pages(
            rows,
            book_id = state["book_id"],
            agentic_run_id = state["agentic_run_id"],
            revision = state["processed_book_revision"],
            cancel_check = context.cancel_check,
            progress_callback = report_pages,
        )
        return {
            "qdrant_collection": collection,
            "embedding_model": model,
            "embedding_dimensions": dimensions,
            "qdrant_point_count": count,
            "qdrant_pages_completed": len(rows),
        }

    return await graphs.rag.service.run_index_node(context, "qdrant", operation)
