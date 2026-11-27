"""Pure schema descriptions for the published BD retrieval projections."""
from __future__ import annotations

import domains
import infra

from typing import Any


def elasticsearch_schema() -> dict[str, Any]:
    """Describe the passage mapping and bounded lexical query contract."""
    mapping = infra.elasticsearch.schemas.BD_RAG_PASSAGES_MAPPING
    return {
        "source": "elasticsearch",
        "index_alias": infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES,
        "properties": mapping["mappings"]["properties"],
        "query": {
            "mode": "BM25 multi_match",
            "search_fields": ["content^2", "section_path.text"],
            "scope_fields": [
                "book_id",
                "agentic_run_id",
                "processed_book_revision",
                "index_profile_fingerprint",
                "book_fingerprint",
            ],
            "agent_query_format": "Natural-language text only; the server builds the query DSL.",
        },
    }


def qdrant_schema() -> dict[str, Any]:
    """Describe the dense/sparse passage collection without exposing its name."""
    return {
        "source": "qdrant",
        "vectors": {
            "dense": "Embedding vector using the model and dimensions pinned by the published index.",
            "sparse": domains.bd.rag.source.indexes.qdrant_index.params.SPARSE_MODEL_NAME,
        },
        "payload_fields": {
            "book_id": "String",
            "extraction_run_id": "String",
            "agentic_run_id": "String",
            "processed_book_revision": "Integer",
            "source_type": "String",
            "index_profile_fingerprint": "String",
            "processed_page_number": "Integer",
            "source_page_number": "Integer",
            "section_path": "List<String>",
            "content_role": "String",
            "content_sha256": "String",
            "chunk_index": "Integer",
            "source_char_start": "Integer",
            "content": "String",
            "embedding_model": "String",
            "embedding_dimensions": "Integer",
            "index_profile_version": "String",
            "chunking_policy_version": "String",
            "chunk_size_chars": "Integer",
            "chunk_overlap_chars": "Integer",
            "chunk_separators": "List<String>",
            "sparse_model": "String",
            "indexed_at": "Number",
        },
        "query": {
            "mode": "Dense and sparse prefetch followed by reciprocal-rank fusion.",
            "scope_fields": [
                "book_id",
                "agentic_run_id",
                "processed_book_revision",
                "index_profile_fingerprint",
            ],
            "agent_query_format": "Natural-language text only; the server builds vectors, filters, and fusion.",
        },
    }


def neo4j_schema() -> dict[str, Any]:
    """Describe the indexed book graph and its enforced scope."""
    params = domains.bd.rag.source.indexes.neo4j_index.params
    return {
        "source": "neo4j",
        "base_labels": ["COELHONexus", "BD"],
        "node_labels_by_kind": params.GRAPH_NODE_LABELS_BY_KIND,
        "common_node_properties": [
            {"name": name, "type": value_type}
            for name, value_type in params.GRAPH_NODE_COMMON_PROPERTIES
        ],
        "node_properties_by_kind": {
            kind: [
                {"name": name, "type": value_type}
                for name, value_type in properties
            ]
            for kind, properties in params.GRAPH_NODE_PROPERTIES_BY_KIND.items()
        },
        "common_relationship_properties": [
            {"name": name, "type": value_type}
            for name, value_type in params.GRAPH_RELATIONSHIP_COMMON_PROPERTIES
        ],
        "optional_relationship_properties": [
            {"name": name, "type": value_type}
            for name, value_type in params.GRAPH_RELATIONSHIP_OPTIONAL_PROPERTIES
        ],
        "structural_relationship_patterns": [
            {
                "source_labels": list(source_labels),
                "type": relationship_type,
                "target_labels": list(target_labels),
            }
            for source_labels, relationship_type, target_labels
            in params.GRAPH_STRUCTURAL_RELATIONSHIP_PATTERNS
        ],
        "semantic_relationships": {
            "pattern": "(:Entity)-[:BD_REL_<predicate>]->(:Entity)",
            "kind_property": (
                "Original semantic relationship kind, normalized from an "
                "open-vocabulary UPPER_SNAKE_CASE predicate."
            ),
        },
        "scope": {
            "required_node_properties": [
                "book_id",
                "agentic_run_id",
                "processed_book_revision",
            ],
            "required_relationship_properties": [
                "book_id",
                "agentic_run_id",
                "processed_book_revision",
            ],
            "access_mode": "READ",
            "cypher_is_agent_supplied": False,
        },
        "query_intent_fields": {
            "entity_terms": "Entity names or concepts to prioritize.",
            "entity_types": (
                "Case-insensitive open-vocabulary values; common types include "
                "Person, Organization, Location, and Concept."
            ),
            "relationship_terms": "Relationship kinds or predicates to prioritize.",
            "relationship_direction": ["either", "outgoing", "incoming"],
        },
    }


def web_search_policy() -> dict[str, Any]:
    """Describe the bounded external-search lane and its evidence contract."""
    return {
        "source": "web_search",
        "provider": "Search MCP connection selected in Settings.",
        "use_when": "Current external facts or corroboration are requested.",
        "limits": {
            "focused_queries_per_agent": 2,
            "results_per_query": 5,
            "requests_per_minute": 10,
        },
        "evidence": {
            "preserve": ["source_id", "url", "provider_id", "query", "accessed_at"],
            "arbitrary_url_fetch": False,
            "keep_external_evidence_distinct": True,
        },
    }
