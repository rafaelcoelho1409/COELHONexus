"""Compose YCS DeepAgents and LangGraph with the existing ingestion domain."""
from __future__ import annotations

import ai
from . import entities

from typing import Any

from langchain_community.graphs.graph_document import GraphDocument, Node, Relationship
from langchain_core.documents import Document


def build_graph_extractor(model: Any, *, run_id: str | None = None) -> entities.GraphExtractor:
    """Build one DeepAgent and one reusable transcript-extraction graph per Neo4j task."""
    return entities.GraphExtractor(
        graph = ai.ycs.graphs.ingestion.graph_extraction.graphs.graph_extraction_graph(),
        agent = ai.ycs.agents.ingestion.graph_extractor.agent.graph_extractor(model),
        run_id = run_id,
    )


async def extract_graph_document(
    extractor: entities.GraphExtractor,
    document: Document,
) -> GraphDocument:
    """Run the evidence-checked graph and adapt accepted candidates to LangChain graph objects."""
    metadata = document.metadata if isinstance(document.metadata, dict) else {}
    state = await extractor.graph.ainvoke(
        {
            "transcript": document.page_content or "",
            "metadata": {
                key: str(metadata.get(key) or "")
                for key in ("video_id", "title", "channel")
            },
        },
        context = ai.ycs.graphs.ingestion.graph_extraction.entities.GraphExtractionContext(
            agent = extractor.agent,
            run_id = extractor.run_id,
        ),
    )
    accepted = ai.ycs.agents.schemas.GraphExtraction.model_validate(
        state.get("accepted_extraction", {}),
    )
    nodes_by_id: dict[str, Node] = {}
    for entity in accepted.entities:
        nodes_by_id.setdefault(
            entity.entity_id.casefold(),
            Node(id = entity.entity_id, type = entity.entity_type),
        )
    relationships: list[Relationship] = []
    for relation in accepted.relationships:
        source = nodes_by_id.get(relation.source_id.casefold())
        target = nodes_by_id.get(relation.target_id.casefold())
        if source is None or target is None:
            continue
        relationships.append(Relationship(
            source = source,
            target = target,
            type = relation.relationship_type,
        ))
    return GraphDocument(
        nodes = list(nodes_by_id.values()),
        relationships = relationships,
        source = document,
    )
