"""LangGraph adapter that transforms processed book pages into a Neo4j graph."""
from __future__ import annotations
import domains
from .... import graphs

from typing import Any

from langgraph.runtime import Runtime


async def index_book_graph(
    state: graphs.rag.states.ProcessedBookIndexState,
    runtime: Runtime[graphs.rag.entities.ProcessedBookIndexDependencies],
) -> dict[str, Any]:
    context = runtime.context

    async def operation() -> dict[str, Any]:
        rows = await domains.bd.ingestion.ai.retrieval_index.service.read_sources(state, context.storage)
        batches = domains.bd.ingestion.ai.retrieval_index.neo4j_index.domain.graph_batches(rows)
        extractions = []
        pages_completed = 0
        await context.progress_callback(
            "neo4j",
            "running",
            {
                "graph_batches_completed": 0,
                "graph_batches_total": len(batches),
                "neo4j_pages_completed": 0,
            },
        )
        for batch_index, (section_title, batch_rows) in enumerate(batches):
            if await context.cancel_check():
                raise RuntimeError("processed-book retrieval indexing was cancelled")
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd",
                run_id = domains.bd.ingestion.ai.retrieval_index.keys.usage_run_id(
                    state["book_id"],
                    state["agentic_run_id"],
                ),
                node_id = "neo4j",
                stage = "agentic_rag",
            ):
                extraction = await domains.bd.ingestion.ai.retrieval_index.neo4j_index.service.extract_graph_facts(
                    context.graph_transformer,
                    batch_rows,
                    section_title = section_title,
                )
            extractions.append({
                "section_title": section_title,
                "rows": batch_rows,
                "extraction": extraction,
            })
            pages_completed += len(batch_rows)
            await context.progress_callback(
                "neo4j",
                "running",
                {
                    "graph_batches_completed": batch_index + 1,
                    "graph_batches_total": len(batches),
                    "neo4j_pages_completed": pages_completed,
                },
            )
        nodes, edges = domains.bd.ingestion.ai.retrieval_index.neo4j_index.domain.build_graph(
            rows,
            extractions,
            book_id = state["book_id"],
            agentic_run_id = state["agentic_run_id"],
            revision = state["processed_book_revision"],
        )
        if await context.cancel_check():
            raise RuntimeError("processed-book retrieval indexing was cancelled")
        await domains.bd.ingestion.ai.retrieval_index.neo4j_index.service.delete_graph(
            state["book_id"],
            state["agentic_run_id"],
            state["processed_book_revision"],
        )
        node_count, relationship_count = await domains.bd.ingestion.ai.retrieval_index.neo4j_index.service.write_graph(
            nodes,
            edges,
            book_id = state["book_id"],
            agentic_run_id = state["agentic_run_id"],
            revision = state["processed_book_revision"],
        )
        return {
            "neo4j_node_count": node_count,
            "neo4j_relationship_count": relationship_count,
            "neo4j_pages_completed": pages_completed,
        }

    return await graphs.rag.service.run_index_node(context, "neo4j", operation)
