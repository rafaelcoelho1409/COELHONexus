"""Shared callable interface for BD source-specialist retrieval."""
from __future__ import annotations
from . import entities, graphs, params

from typing import Any


async def retrieve(
    book_id: str,
    run_id: str,
    query: str,
    *,
    model: Any = None,
    include_web: bool = False,
) -> dict[str, Any]:
    """Run the four source nodes and return fused evidence with per-source status."""
    graph = graphs.retrieval_graph()
    return await graph.ainvoke(
        {"query": query, "include_web": include_web},
        context = entities.RetrievalContext(
            book_id = book_id,
            run_id = run_id,
            model = model,
        ),
        config = {"max_concurrency": params.MAX_CONCURRENT_SOURCES},
    )
