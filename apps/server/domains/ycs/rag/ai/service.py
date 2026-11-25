"""Run graph extraction and validate transcript-grounded facts."""
from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j.graphs.graph_document import GraphDocument, Node, Relationship

import ai
from ai.ycs.agents.rag.graph_extractor import agent, service as graph_extraction

from .. import source
from . import entities

logger = logging.getLogger(__name__)


def graph_extractor_runtime(
    model: Any,
    *,
    run_id: str | None = None,
) -> entities.GraphExtractorRuntime:
    """Create the runtime state shared by one task's transcript extractions."""
    return entities.GraphExtractorRuntime(
        model=agent.graph_model(model, run_id=run_id),
        run_id=run_id,
    )


def _entity_evidence(name: str, transcript: str) -> str:
    """Return an exact sentence containing an extracted entity name."""
    escaped_name = r"\s+".join(re.escape(part) for part in name.split())
    if not escaped_name:
        return ""
    name_pattern = re.compile(rf"(?<!\w){escaped_name}(?!\w)", re.IGNORECASE)
    for sentence in re.finditer(r"[^.!?\n]+(?:[.!?]+|$)", transcript):
        excerpt = sentence.group().strip()
        if name_pattern.search(excerpt) and len(excerpt) <= 800:
            return excerpt
    match = name_pattern.search(transcript)
    if match is None:
        return ""
    start = max(0, match.start() - 200)
    end = min(len(transcript), match.end() + 200)
    return transcript[start:end].strip()[:800]


def _graph_document_from_extraction(extraction: Any, source_document: Document) -> GraphDocument:
    """Adapt validated Docling Graph facts into the existing Neo4j writer type."""
    entities_by_id: dict[str, Node] = {}

    def get_node(entity_id: str, entity_type: str) -> Node:
        key = entity_id.casefold()
        node = entities_by_id.get(key)
        if node is None:
            node = Node(
                id=entity_id,
                type=entity_type or "Entity",
                properties={"evidence": _entity_evidence(entity_id, source_document.page_content or "")},
            )
            entities_by_id[key] = node
        return node

    for entity in extraction.entities:
        get_node(entity["id"], entity["type"])

    relationships: list[Relationship] = []
    for fact in extraction.relationships:
        source_node = get_node(fact["source_id"], fact.get("source_type") or "Entity")
        target_node = get_node(fact["target_id"], fact.get("target_type") or "Entity")
        properties = fact.get("properties") or {}
        relationships.append(Relationship(
            source=source_node,
            target=target_node,
            type=fact["type"],
            properties={"evidence": str(properties.get("evidence") or "")},
        ))

    return GraphDocument(
        nodes=list(entities_by_id.values()),
        relationships=relationships,
        source=source_document,
    )


async def _process(
    runtime: entities.GraphExtractorRuntime,
    document: Document,
    *,
    additional_instructions: str = "",
) -> Any:
    """Run Docling Graph and adapt its projection for YCS validation."""
    instructions = "\n\n".join(filter(None, (
        ai.ycs.prompts.rag.graph_extractor.prompts.SYSTEM_PROMPT,
        additional_instructions.strip(),
    )))
    return await graph_extraction.aextract_text(
        document.page_content or "",
        runtime.model,
        instructions=instructions,
        source_name="ycs-transcript.md",
    )


def _validated_graph_document(
    candidate: GraphDocument,
    source_document: Document,
    *,
    require_evidence: bool,
) -> tuple[GraphDocument, list[str]]:
    """Validate candidates and adapt accepted facts to LangChain objects."""
    nodes, relations, issues = source.indexes.neo4j_index.domain.validate_graph_candidates(
        candidate,
        source_document.page_content or "",
        require_evidence = require_evidence,
    )
    source_nodes = {
        str(getattr(item, "id", "")).casefold(): item
        for item in candidate.nodes or []
    }
    nodes_by_id: dict[str, Node] = {}
    for item in nodes:
        original = source_nodes.get(item["id"].casefold())
        properties = getattr(original, "properties", {}) or {}
        nodes_by_id[item["id"].casefold()] = Node(
            id=item["id"],
            type=item["type"],
            properties={"evidence": properties.get("evidence", "")},
        )
    source_relationships = {
        (
            str(getattr(getattr(item, "source", None), "id", "")).casefold(),
            str(getattr(item, "type", "")).casefold(),
            str(getattr(getattr(item, "target", None), "id", "")).casefold(),
        ): item
        for item in candidate.relationships or []
    }
    accepted_relationships: list[Relationship] = []
    for relation in relations:
        key = (
            relation["source_id"].casefold(),
            relation["type"].casefold(),
            relation["target_id"].casefold(),
        )
        original = source_relationships.get(key)
        properties = getattr(original, "properties", {}) or {}
        accepted_relationships.append(Relationship(
            source=nodes_by_id[key[0]],
            target=nodes_by_id[key[2]],
            type=relation["type"],
            properties={"evidence": properties.get("evidence", "")},
        ))
    return GraphDocument(
        nodes = list(nodes_by_id.values()),
        relationships = accepted_relationships,
        source = source_document,
    ), issues


async def extract_graph_document(
    runtime: entities.GraphExtractorRuntime,
    document: Document,
) -> GraphDocument:
    """Extract one transcript, validate facts, and preserve its source tag."""
    extraction = await _process(runtime, document)
    candidate = _graph_document_from_extraction(extraction, document)
    accepted, issues = _validated_graph_document(
        candidate,
        document,
        require_evidence = True,
    )
    if issues:
        feedback = (
            f"{len(issues)} candidate(s) were rejected because they lacked "
            "exact source evidence, referenced missing endpoints, had "
            "conflicting types, or exceeded extraction limits."
        )
        try:
            retry_extraction = await _process(
                runtime,
                document,
                additional_instructions = (
                    "Correct the prior unsupported candidates using only exact "
                    "transcript evidence. Every fact must include a short excerpt "
                    "copied verbatim from the transcript; include entities only "
                    "when they occur in the source. "
                    "Validation feedback:\n" + feedback
                ),
            )
        except Exception as error:
            logger.warning(
                "[ycs:neo4j] Docling Graph evidence retry failed (%s); keeping the validated first pass",
                type(error).__name__,
            )
            return accepted
        retry_candidate = _graph_document_from_extraction(retry_extraction, document)
        retried, retry_issues = _validated_graph_document(
            retry_candidate,
            document,
            require_evidence = True,
        )
        if (
            len(retried.nodes) + len(retried.relationships)
            > len(accepted.nodes) + len(accepted.relationships)
        ):
            accepted = retried
        if retry_issues:
            logger.info(
                "[ycs:neo4j] Docling Graph retained %d unsupported candidate(s) after evidence retry",
                len(retry_issues),
            )
    return accepted
