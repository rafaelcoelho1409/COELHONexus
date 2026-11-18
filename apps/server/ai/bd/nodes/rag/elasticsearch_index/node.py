"""LangGraph adapter for full-text indexing of processed pages."""
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
        rows = await domains.bd.rag.source.storage.service.read_sources(state, context.storage)

        async def report_pages(completed: int, _total: int) -> None:
            await context.progress_callback(
                "elasticsearch",
                "running",
                {"elasticsearch_pages_completed": completed},
            )

        index, count, passage_index, passage_count = await domains.bd.rag.source.indexes.elasticsearch_index.service.index_pages(
            rows,
            book_id = state["book_id"],
            agentic_run_id = state["agentic_run_id"],
            revision = state["processed_book_revision"],
            progress_callback = report_pages,
            cancel_check = context.cancel_check,
        )
        return {
            "elasticsearch_index": index,
            "elasticsearch_document_count": count,
            "elasticsearch_passage_index": passage_index,
            "elasticsearch_passage_count": passage_count,
            "elasticsearch_pages_completed": count,
        }

    return await graphs.rag.service.run_index_node(context, "elasticsearch", operation)
