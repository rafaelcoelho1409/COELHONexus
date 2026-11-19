"""Application service for Neo4j graph-indexing orchestration."""
from __future__ import annotations
import domains
from .... import graphs

import asyncio
from collections.abc import Coroutine, Iterable
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j import LLMGraphTransformer


async def extract_graph_facts(
    graph_transformer: LLMGraphTransformer,
    rows: list[dict[str, Any]],
    *,
    section_title: str,
) -> dict[str, list[dict[str, Any]]]:
    """Extract graph facts from one book section for the Neo4j indexing node."""
    document = Document(
        page_content = (
            f"Book section: {section_title}\n\n"
            f"{domains.bd.rag.source.indexes.neo4j_index.domain.source_material(rows)}"
        ),
    )
    candidate = await graph_transformer.aprocess_response(document)
    return domains.bd.rag.source.indexes.neo4j_index.domain.graph_facts(candidate)


async def index_book_graph(
    graph_state: graphs.rag.states.ProcessedBookIndexState,
    context: graphs.rag.entities.ProcessedBookIndexDependencies,
) -> dict[str, Any]:
    rows = await domains.bd.rag.source.storage.service.read_sources(graph_state, context.storage)
    batches = domains.bd.rag.source.indexes.neo4j_index.domain.graph_batches(rows)
    all_pages = {row["processed_page_number"] for row in rows}
    chunks_remaining_by_page: dict[int, int] = {}
    for _, batch_rows in batches:
        for row in batch_rows:
            page_number = row["processed_page_number"]
            chunks_remaining_by_page[page_number] = chunks_remaining_by_page.get(page_number, 0) + 1
    completed_pages = all_pages - chunks_remaining_by_page.keys()
    pages_completed = len(completed_pages)
    pages_total = len(all_pages)
    node_write_batches_completed = 0
    node_write_batches_total = 0
    relationship_write_batches_completed = 0
    relationship_write_batches_total = 0
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "extracting",
            "neo4j_stage_completed": pages_completed,
            "neo4j_stage_total": pages_total,
            "graph_batches_completed": 0,
            "graph_batches_total": len(batches),
            "neo4j_pages_completed": pages_completed,
        },
    )
    extraction_semaphore = asyncio.Semaphore(domains.bd.rag.ai.params.GRAPH_EXTRACTION_CONCURRENCY)
    progress_lock = asyncio.Lock()
    completed_batches = 0

    async def extract_batch(
        section_title: str,
        batch_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        nonlocal completed_batches, pages_completed
        async with extraction_semaphore:
            if await context.cancel_check():
                raise RuntimeError("processed-book retrieval indexing was cancelled")
            with domains.settings.llm_counter.service.llm_context(
                ns = "bd",
                run_id = domains.bd.rag.ai.keys.usage_run_id(
                    graph_state["book_id"],
                    graph_state["agentic_run_id"],
                ),
                node_id = "neo4j",
                stage = "agentic_rag",
            ):
                extraction = await extract_graph_facts(
                    context.graph_transformer,
                    batch_rows,
                    section_title = section_title,
                )

        async with progress_lock:
            for row in batch_rows:
                page_number = row["processed_page_number"]
                chunks_remaining_by_page[page_number] -= 1
                if chunks_remaining_by_page[page_number] == 0 and page_number not in completed_pages:
                    completed_pages.add(page_number)
                    pages_completed += 1
            completed_batches += 1
            await context.progress_callback(
                "neo4j",
                "running",
                {
                    "neo4j_stage": "extracting",
                    "neo4j_stage_completed": pages_completed,
                    "neo4j_stage_total": pages_total,
                    "graph_batches_completed": completed_batches,
                    "graph_batches_total": len(batches),
                    "neo4j_pages_completed": pages_completed,
                },
            )
        return {"section_title": section_title, "rows": batch_rows, "extraction": extraction}

    extractions = await _gather_cancel_on_failure(
        extract_batch(section_title, batch_rows)
        for section_title, batch_rows in batches
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "building_graph",
            "neo4j_stage_completed": 0,
            "neo4j_stage_total": 1,
            "graph_batches_completed": len(batches),
            "graph_batches_total": len(batches),
            "neo4j_pages_completed": pages_completed,
        },
    )
    nodes, edges = domains.bd.rag.source.indexes.neo4j_index.domain.build_graph(
        rows,
        extractions,
        book_id = graph_state["book_id"],
        agentic_run_id = graph_state["agentic_run_id"],
        revision = graph_state["processed_book_revision"],
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "building_graph",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
        },
    )
    if await context.cancel_check():
        raise RuntimeError("processed-book retrieval indexing was cancelled")
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "clearing_graph",
            "neo4j_stage_completed": 0,
            "neo4j_stage_total": 1,
        },
    )
    await domains.bd.rag.source.indexes.neo4j_index.service.delete_graph(
        graph_state["book_id"],
        graph_state["agentic_run_id"],
        graph_state["processed_book_revision"],
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "clearing_graph",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
        },
    )

    async def report_write_progress(stage: str, completed: int, total: int) -> None:
        nonlocal node_write_batches_completed, node_write_batches_total
        nonlocal relationship_write_batches_completed, relationship_write_batches_total
        details = {
            "neo4j_stage": stage,
            "neo4j_stage_completed": completed,
            "neo4j_stage_total": total,
        }
        if stage == "writing_nodes":
            node_write_batches_completed = completed
            node_write_batches_total = total
            details.update(
                neo4j_node_write_batches_completed=completed,
                neo4j_node_write_batches_total=total,
            )
        elif stage == "writing_relationships":
            relationship_write_batches_completed = completed
            relationship_write_batches_total = total
            details.update(
                neo4j_relationship_write_batches_completed=completed,
                neo4j_relationship_write_batches_total=total,
            )
        await context.progress_callback("neo4j", "running", details)

    node_count, relationship_count = await domains.bd.rag.source.indexes.neo4j_index.service.write_graph(
        nodes,
        edges,
        book_id = graph_state["book_id"],
        agentic_run_id = graph_state["agentic_run_id"],
        revision = graph_state["processed_book_revision"],
        progress_callback = report_write_progress,
        cancel_check = context.cancel_check,
    )
    await context.progress_callback(
        "neo4j",
        "running",
        {
            "neo4j_stage": "complete",
            "neo4j_stage_completed": 1,
            "neo4j_stage_total": 1,
            "neo4j_node_write_batches_completed": node_write_batches_completed,
            "neo4j_node_write_batches_total": node_write_batches_total,
            "neo4j_relationship_write_batches_completed": relationship_write_batches_completed,
            "neo4j_relationship_write_batches_total": relationship_write_batches_total,
        },
    )
    return {
        "neo4j_node_count": node_count,
        "neo4j_relationship_count": relationship_count,
        "neo4j_pages_completed": pages_completed,
        "neo4j_stage": "complete",
        "neo4j_stage_completed": 1,
        "neo4j_stage_total": 1,
        "neo4j_node_write_batches_completed": node_write_batches_completed,
        "neo4j_node_write_batches_total": node_write_batches_total,
        "neo4j_relationship_write_batches_completed": relationship_write_batches_completed,
        "neo4j_relationship_write_batches_total": relationship_write_batches_total,
    }


async def _gather_cancel_on_failure(awaitables: Iterable[Coroutine[Any, Any, Any]]) -> list[Any]:
    tasks = [asyncio.create_task(awaitable) for awaitable in awaitables]
    try:
        return await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions = True)
        raise
