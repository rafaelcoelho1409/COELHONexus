"""Idempotent, version-scoped Neo4j writes for processed-book graph records."""
from __future__ import annotations
import infra
from . import domain, entities, params

import json
from collections.abc import Awaitable, Callable
from typing import Any


WriteProgressReporter = Callable[[str, int, int], Awaitable[None]]
CancelCheck = Callable[[], Awaitable[bool]]


async def write_graph(
    nodes: list[entities.GraphNode],
    edges: list[entities.GraphEdge],
    *,
    book_id: str,
    agentic_run_id: str,
    revision: int,
    progress_callback: WriteProgressReporter | None = None,
    cancel_check: CancelCheck | None = None,
) -> tuple[int, int]:
    if progress_callback is not None:
        await progress_callback("preparing_writes", 0, 1)
    node_groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        label = params.GRAPH_NODE_LABELS_BY_KIND.get(node.kind)
        if label is None:
            raise ValueError(f"Unsupported BD graph node kind: {node.kind}")
        node_groups.setdefault(label, []).append({
            "id": node.id,
            "kind": node.kind,
            "name": node.name,
            "properties": node.properties,
        })
    edge_groups: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        relationship_type = domain.neo4j_relationship_type(edge.kind, semantic = edge.semantic)
        properties = dict(edge.properties)
        properties["citations"] = json.dumps(
            properties.get("citations", []),
            ensure_ascii = False,
        )
        edge_groups.setdefault(relationship_type, []).append({
            "id": edge.id,
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "kind": edge.kind,
            "properties": properties,
        })
    if progress_callback is not None:
        await progress_callback("preparing_writes", 1, 1)
    async with infra.neo4j.service.session() as session:
        node_batch_count = sum(
            (len(rows) + params.GRAPH_WRITE_BATCH_SIZE - 1) // params.GRAPH_WRITE_BATCH_SIZE
            for rows in node_groups.values()
        )
        if progress_callback is not None:
            await progress_callback("writing_nodes", 0, node_batch_count)
        node_batch_index = 0
        for label, node_rows in node_groups.items():
            for offset in range(0, len(node_rows), params.GRAPH_WRITE_BATCH_SIZE):
                if cancel_check is not None and await cancel_check():
                    raise RuntimeError("processed-book retrieval indexing was cancelled")

                async def write_nodes(transaction, label = label, node_rows = node_rows, offset = offset):
                    result = await transaction.run(
                        f"""
                        UNWIND $rows AS row
                        MERGE (node:COELHONexus:BD {{id: row.id}})
                        SET node:{label},
                            node.kind = row.kind,
                            node.name = row.name,
                            node += row.properties,
                            node.book_id = $book_id,
                            node.agentic_run_id = $agentic_run_id,
                            node.processed_book_revision = $revision
                        REMOVE node:BDNode
                        """,
                        rows = node_rows[offset : offset + params.GRAPH_WRITE_BATCH_SIZE],
                        book_id = book_id,
                        agentic_run_id = agentic_run_id,
                        revision = revision,
                    )
                    await result.consume()
                await session.execute_write(write_nodes)
                node_batch_index += 1
                if progress_callback is not None:
                    await progress_callback("writing_nodes", node_batch_index, node_batch_count)

        edge_batch_count = sum(
            (len(rows) + params.GRAPH_WRITE_BATCH_SIZE - 1) // params.GRAPH_WRITE_BATCH_SIZE
            for rows in edge_groups.values()
        )
        if edge_groups and progress_callback is not None:
            await progress_callback("writing_relationships", 0, edge_batch_count)
        edge_batch_index = 0
        for relationship_type, edge_rows in edge_groups.items():
            for offset in range(0, len(edge_rows), params.GRAPH_WRITE_BATCH_SIZE):
                if cancel_check is not None and await cancel_check():
                    raise RuntimeError("processed-book retrieval indexing was cancelled")

                async def write_edges(
                    transaction,
                    relationship_type = relationship_type,
                    edge_rows = edge_rows,
                    offset = offset,
                ):
                    result = await transaction.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (source:COELHONexus:BD {{
                            id: row.source_id, book_id: $book_id, agentic_run_id: $agentic_run_id,
                            processed_book_revision: $revision
                        }})
                        MATCH (target:COELHONexus:BD {{
                            id: row.target_id, book_id: $book_id, agentic_run_id: $agentic_run_id,
                            processed_book_revision: $revision
                        }})
                        MERGE (source)-[relationship:{relationship_type} {{id: row.id}}]->(target)
                        SET relationship.kind = row.kind,
                            relationship += row.properties,
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
                edge_batch_index += 1
                if progress_callback is not None:
                    await progress_callback("writing_relationships", edge_batch_index, edge_batch_count)
    return sum(map(len, node_groups.values())), sum(map(len, edge_groups.values()))


async def delete_graph(book_id: str, agentic_run_id: str, revision: int) -> int:
    """Delete only graph records owned by one immutable processed-book revision."""
    async with infra.neo4j.service.session() as session:
        async def delete_nodes(transaction):
            result = await transaction.run(
                """
                MATCH (node:COELHONexus:BD {
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
