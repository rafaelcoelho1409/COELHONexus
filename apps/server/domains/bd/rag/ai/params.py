"""Operational limits and identifiers for the Agentic RAG workflow."""
from __future__ import annotations

from typing import Any

GRAPH_TRANSFORMER_TIMEOUT_S = 650.0
GRAPH_EXTRACTION_CONCURRENCY = 2
GRAPH_EXTRACTION_RETRY_ATTEMPTS = 3
GRAPH_EXTRACTION_RETRY_INITIAL_INTERVAL_S = 5.0
GRAPH_EXTRACTION_RETRY_MAX_INTERVAL_S = 30.0
GRAPH_ROTATOR_TASK = "bd_ingestion_agentic_rag_neo4j"
RUN_LOCK_LEASE_S = 180
RUN_LOCK_RENEW_S = 45
RUN_LOCK_RETRY_S = 10
CANCEL_FLAG_TTL_S = 86400
STALE_JOB_AFTER_S = 1800
# Keep the registered Celery ID stable across the domain-module move.
TASK_NAME = "domains.bd.ingestion.ai.retrieval_index.task.index_processed_book"
NODE_ORDER = (
    "pin_sources",
    "elasticsearch",
    "qdrant",
    "neo4j",
    "publish_retrieval_index",
)
REPLAY_PREREQUISITES = {
    "pin_sources": frozenset(),
    "elasticsearch": frozenset({"pin_sources"}),
    "qdrant": frozenset({"elasticsearch", "neo4j"}),
    "neo4j": frozenset({"elasticsearch", "qdrant"}),
    "publish_retrieval_index": frozenset({"qdrant", "neo4j"}),
}
REPLAY_DESCENDANTS = {
    "pin_sources": frozenset(NODE_ORDER),
    "elasticsearch": frozenset(NODE_ORDER[1:]),
    "qdrant": frozenset({"qdrant", "publish_retrieval_index"}),
    "neo4j": frozenset({"neo4j", "publish_retrieval_index"}),
    "publish_retrieval_index": frozenset({"publish_retrieval_index"}),
}
NODE_OUTPUT_DEFAULTS: dict[str, dict[str, Any]] = {
    "pin_sources": {"source_artifact_key": "", "pin_sources_pages_completed": 0},
    "elasticsearch": {
        "elasticsearch_index": "",
        "elasticsearch_document_count": 0,
        "elasticsearch_passage_index": "",
        "elasticsearch_passage_count": 0,
        "elasticsearch_pages_completed": 0,
    },
    "qdrant": {
        "qdrant_collection": "",
        "embedding_model": "",
        "embedding_dimensions": 0,
        "qdrant_point_count": 0,
        "qdrant_pages_completed": 0,
    },
    "neo4j": {
        "neo4j_node_count": 0,
        "neo4j_relationship_count": 0,
        "graph_batches_completed": 0,
        "graph_batches_total": 0,
        "neo4j_pages_completed": 0,
        "neo4j_llm_calls_active": 0,
        "neo4j_stage": "idle",
        "neo4j_stage_completed": 0,
        "neo4j_stage_total": 0,
        "neo4j_node_write_batches_completed": 0,
        "neo4j_node_write_batches_total": 0,
        "neo4j_relationship_write_batches_completed": 0,
        "neo4j_relationship_write_batches_total": 0,
    },
    "publish_retrieval_index": {},
}
LLM_COUNTER_NODES_BY_GRAPH_NODE = {
    node: frozenset({"neo4j"}) if node == "neo4j" else frozenset()
    for node in NODE_ORDER
}
