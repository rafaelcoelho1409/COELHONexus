"""LangGraph adapter for pinning the finalized processed-book pages."""
from __future__ import annotations
import domains
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def pin_sources(
    state: graphs.rag.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.rag.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context

    async def operation() -> dict[str, Any]:
        async def report_pages(completed: int, _total: int) -> None:
            await context.progress_callback(
                "pin_sources",
                "running",
                {"pin_sources_pages_completed": completed},
            )

        return await domains.bd.rag.pin_sources.service.pin_sources(
            state,
            context.storage,
            progress_callback = report_pages,
            cancel_check = context.cancel_check,
        )

    return await graphs.rag.service.run_index_node(
        context,
        "pin_sources",
        operation,
    )
