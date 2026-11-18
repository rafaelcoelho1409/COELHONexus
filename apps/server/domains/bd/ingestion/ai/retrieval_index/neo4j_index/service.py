"""Idempotent, version-scoped Neo4j writes for processed-book graph records."""
from __future__ import annotations

from typing import Any

import domains
import infra
from langchain_core.documents import Document
from langchain_neo4j import LLMGraphTransformer

from .. import params
from . import domain


def build_graph_transformer(model: Any) -> LLMGraphTransformer:
    """Build the YCS-compatible transformer with the configured chat model."""
    model = model.with_config(config = {
        "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
    })
    return LLMGraphTransformer(
        llm = model,
        ignore_tool_usage = True,
        strict_mode = False,
    )


async def extract_graph_facts(
    graph_transformer: LLMGraphTransformer,
    rows: list[dict[str, Any]],
    *,
    section_title: str,
) -> dict[str, list[dict[str, str]]]:
    """Use LangChain's graph transformation and return plain graph facts."""
    document = Document(
        page_content = f"Book section: {section_title}\n\n{domain.source_material(rows)}",
    )
    candidate = await graph_transformer.aprocess_response(document)
    return domain.graph_facts(candidate)


async def write_graph(
    nodes: list[domain.GraphNode],
    edges: list[domain.GraphEdge],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
) -> tuple[int, int]:
    node_rows = [
        {"id": node.id, "kind": node.kind, "name": node.name, "properties": node.properties}
        for node in nodes
    ]
    edge_rows = [
        {
            "id": edge.id,
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "kind": edge.kind,
        }
        for edge in edges
    ]
    async with infra.neo4j.service.session() as session:
        for offset in range(0, len(node_rows), params.GRAPH_WRITE_BATCH_SIZE):
            async def write_nodes(transaction):
                result = await transaction.run(
                    """
                    UNWIND $rows AS row
                    MERGE (node:COELHONexus:BD:BDNode {id: row.id})
                    SET node.kind = row.kind,
                        node.name = row.name,
                        node.book_id = $book_id,
                        node.agentic_run_id = $agentic_run_id,
                        node.processed_book_revision = $revision,
                        node += row.properties
                    """,
                    rows = node_rows[offset : offset + params.GRAPH_WRITE_BATCH_SIZE],
                    book_id = book_id,
                    agentic_run_id = agentic_run_id,
                    revision = revision,
                )
                await result.consume()
            await session.execute_write(write_nodes)
        for offset in range(0, len(edge_rows), params.GRAPH_WRITE_BATCH_SIZE):
            async def write_edges(transaction):
                result = await transaction.run(
                    """
                    UNWIND $rows AS row
                    MATCH (source:COELHONexus:BD:BDNode {
                        id: row.source_id, book_id: $book_id, agentic_run_id: $agentic_run_id,
                        processed_book_revision: $revision
                    })
                    MATCH (target:COELHONexus:BD:BDNode {
                        id: row.target_id, book_id: $book_id, agentic_run_id: $agentic_run_id,
                        processed_book_revision: $revision
                    })
                    MERGE (source)-[relationship:BD_RELATES {id: row.id}]->(target)
                    SET relationship.kind = row.kind,
                        relationship.book_id = $book_id,
                        relationship.agentic_run_id = $agentic_run_id,
                        relationship.processed_book_revision = $revision
                    """,
                    rows = edge_rows[offset : offset + params.GRAPH_WRITE_BATCH_SIZE],
                    book_id = book_id,
                    agentic_run_id = agentic_run_id,
                    revision = revision,
                )
                await result.consume()
            await session.execute_write(write_edges)
    return len(node_rows), len(edge_rows)


async def delete_graph(book_id: str, agentic_run_id: str, revision: int) -> int:
    """Delete only graph records owned by one immutable processed-book revision."""
    async with infra.neo4j.service.session() as session:
        async def delete_nodes(transaction):
            result = await transaction.run(
                """
                MATCH (node:COELHONexus:BD:BDNode {
                    book_id: $book_id, agentic_run_id: $agentic_run_id,
                    processed_book_revision: $revision
                })
                WITH collect(node) AS nodes, count(node) AS deleted
                FOREACH (node IN nodes | DETACH DELETE node)
                RETURN deleted
                """,
                book_id = book_id,
                agentic_run_id = agentic_run_id,
                revision = revision,
            )
            record = await result.single()
            return record["deleted"] if record else 0
        return await session.execute_write(delete_nodes)
