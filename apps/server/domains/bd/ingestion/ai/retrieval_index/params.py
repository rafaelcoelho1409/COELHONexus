"""Operational limits and identifiers for the processed-book retrieval index."""
from __future__ import annotations

# Qdrant chunking values are character counts, aligned with YCS defaults.
CHUNK_SIZE = 2000
CHUNK_OVERLAP = 0
QDRANT_INDEX_PROFILE_VERSION = "1"
QDRANT_CHUNKING_VERSION = "recursive_character_v1"
QDRANT_CHUNK_SEPARATORS = ("\n\n", "\n", " ", "")
PAGE_PROGRESS_INTERVAL = 25
EMBED_BATCH_SIZE = 50
GRAPH_BATCH_CHARS = 18000
GRAPH_WRITE_BATCH_SIZE = 500
GRAPH_TRANSFORMER_TIMEOUT_S = 650.0
GRAPH_ROTATOR_TASK = "bd_ingestion_agentic_rag_neo4j"
SPARSE_MODEL_NAME = "Qdrant/bm25"
QDRANT_COLLECTION_PREFIX = "coelhonexus-bd-rag"
RUN_LOCK_LEASE_S = 180
RUN_LOCK_RENEW_S = 45
RUN_LOCK_RETRY_S = 10
CANCEL_FLAG_TTL_S = 86400
STALE_JOB_AFTER_S = 1800
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
LLM_COUNTER_NODES_BY_GRAPH_NODE = {
    node: frozenset({"neo4j"}) if node == "neo4j" else frozenset()
    for node in NODE_ORDER
}
