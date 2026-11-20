"""Run graph extraction and validate transcript-grounded facts."""
from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_neo4j import LLMGraphTransformer
from langchain_neo4j.graphs.graph_document import GraphDocument, Node, Relationship

from ai.ycs.agents.rag.graph_transformer import agent

from .. import source
from . import entities

logger = logging.getLogger(__name__)


def graph_transformer_runtime(
    model: Any,
    *,
    run_id: str | None = None,
) -> entities.GraphTransformerRuntime:
    """Create the runtime state shared by one task's transcript transforms."""
    return entities.GraphTransformerRuntime(model = model, run_id = run_id)


def _transformer(
    runtime: entities.GraphTransformerRuntime,
    *,
    additional_instructions: str = "",
    unstructured: bool = False,
) -> LLMGraphTransformer:
    """Create a transformer for the requested provider-compatibility mode."""
    return agent.graph_transformer(
        runtime.model,
        run_id = runtime.run_id,
        additional_instructions = additional_instructions,
        unstructured = unstructured,
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
    nodes_by_id = {
        item["id"].casefold(): Node(id = item["id"], type = item["type"])
        for item in nodes
    }
    accepted_relationships = [
        Relationship(
            source = nodes_by_id[relation["source_id"].casefold()],
            target = nodes_by_id[relation["target_id"].casefold()],
            type = relation["type"],
        )
        for relation in relations
    ]
    return GraphDocument(
        nodes = list(nodes_by_id.values()),
        relationships = accepted_relationships,
        source = source_document,
    ), issues


def _is_structured_schema_error(error: Exception) -> bool:
    message = f"{type(error).__name__}: {error}".casefold()
    return any(marker in message for marker in (
        "dynamicgraph",
        "anyof",
        "json schema",
        "structured output",
        "invalid schema",
        "schema validation",
        "function_calling",
        "function calling",
        "tool call schema",
        # Some OpenAI-compatible gateways return this internal 500 while
        # parsing structured graph output instead of reporting a schema error.
        "nonetype' object has no attribute 'get'",
    ))


async def _process(
    runtime: entities.GraphTransformerRuntime,
    document: Document,
    *,
    additional_instructions: str = "",
) -> tuple[GraphDocument, bool]:
    """Return a candidate and whether provider compatibility fallback ran."""
    try:
        transformer = _transformer(
            runtime,
            additional_instructions = additional_instructions,
        )
        result = await transformer.aprocess_response(document)
        return result, False
    except Exception as error:
        if not _is_structured_schema_error(error):
            raise
        logger.warning(
            "[ycs:neo4j] provider rejected structured graph output; "
            "retrying with the compatibility parser (%s)",
            type(error).__name__,
        )
    transformer = _transformer(
        runtime,
        additional_instructions = additional_instructions,
        unstructured = True,
    )
    return await transformer.aprocess_response(document), True


async def extract_graph_document(
    runtime: entities.GraphTransformerRuntime,
    document: Document,
) -> GraphDocument:
    """Transform one transcript, validate facts, and preserve its source tag."""
    candidate, compatibility_fallback = await _process(runtime, document)
    if not compatibility_fallback and not (candidate.nodes or candidate.relationships):
        candidate = await _transformer(runtime, unstructured = True).aprocess_response(document)
        compatibility_fallback = True
        logger.warning("[ycs:neo4j] structured graph output was empty; used compatibility parser")

    accepted, issues = _validated_graph_document(
        candidate,
        document,
        require_evidence = not compatibility_fallback,
    )
    if issues and not compatibility_fallback:
        feedback = (
            f"{len(issues)} candidate(s) were rejected because they lacked "
            "exact source evidence, referenced missing endpoints, had "
            "conflicting types, or exceeded extraction limits."
        )
        retry_candidate, retry_fallback = await _process(
            runtime,
            document,
            additional_instructions = (
                "Correct the prior unsupported candidates using only exact "
                "transcript evidence. Every node and relationship must have "
                "an evidence property copied verbatim from the transcript. "
                "Validation feedback:\n" + feedback
            ),
        )
        retried, retry_issues = _validated_graph_document(
            retry_candidate,
            document,
            require_evidence = not retry_fallback,
        )
        if (
            len(retried.nodes) + len(retried.relationships)
            > len(accepted.nodes) + len(accepted.relationships)
        ):
            accepted = retried
        if retry_issues:
            logger.info(
                "[ycs:neo4j] transformer retained %d unsupported candidate(s) after evidence retry",
                len(retry_issues),
            )
        if (
            not retry_fallback
            and not retried.nodes
            and not retried.relationships
            and (retry_candidate.nodes or retry_candidate.relationships)
        ):
            fallback_candidate = await _transformer(
                runtime,
                additional_instructions = (
                    "Return only transcript-supported entities and direct "
                    "relationships. Omit facts whose named endpoints do not "
                    "appear together in one sentence."
                ),
                unstructured = True,
            ).aprocess_response(document)
            fallback_graph, _ = _validated_graph_document(
                fallback_candidate,
                document,
                require_evidence = False,
            )
            if fallback_graph.nodes or fallback_graph.relationships:
                accepted = fallback_graph
                logger.warning(
                    "[ycs:neo4j] evidence validation yielded no facts; "
                    "accepted locally grounded compatibility output",
                )
    return accepted
