"""Store names and limits for the BD retrieval query workbench."""
from __future__ import annotations

import infra

from ... import rag

APP_BD = "bd"

BACKEND_ELASTICSEARCH = "elasticsearch"
BACKEND_QDRANT = "qdrant"
BACKEND_NEO4J = "neo4j"
BACKENDS = (BACKEND_ELASTICSEARCH, BACKEND_QDRANT, BACKEND_NEO4J)

ELASTICSEARCH_INDEX_NAMES = (
    infra.elasticsearch.keys.INDEX_BD_INGESTION,
    infra.elasticsearch.keys.INDEX_BD_RAG_PASSAGES,
)
QDRANT_COLLECTION_PREFIX = rag.source.indexes.qdrant_index.params.QDRANT_COLLECTION_PREFIX
QDRANT_MAX_COLLECTIONS = 50
MAX_QUERY_BODY_CHARS = 100_000

QDRANT_PAYLOAD_KEYS = (
    "book_id",
    "extraction_run_id",
    "agentic_run_id",
    "processed_book_revision",
    "source_type",
    "processed_page_number",
    "source_page_number",
    "section_path",
    "content_role",
    "content_sha256",
    "chunk_index",
    "source_char_start",
    "content",
    "embedding_model",
    "embedding_dimensions",
    "index_profile_version",
    "chunking_policy_version",
    "chunk_size_chars",
    "chunk_overlap_chars",
    "chunk_separators",
    "sparse_model",
    "index_profile_fingerprint",
)

NEO4J_BASE_LABELS = ("COELHONexus", "BD")
NEO4J_NODE_LABELS = tuple(rag.source.indexes.neo4j_index.params.GRAPH_NODE_LABELS_BY_KIND.values())
NEO4J_COMMON_NODE_PROPERTIES = rag.source.indexes.neo4j_index.params.GRAPH_NODE_COMMON_PROPERTIES
NEO4J_NODE_PROPERTIES_BY_LABEL = {
    rag.source.indexes.neo4j_index.params.GRAPH_NODE_LABELS_BY_KIND[kind]: (
        *NEO4J_COMMON_NODE_PROPERTIES,
        *properties,
    )
    for kind, properties in rag.source.indexes.neo4j_index.params.GRAPH_NODE_PROPERTIES_BY_KIND.items()
}
NEO4J_RELATIONSHIP_PATTERNS = rag.source.indexes.neo4j_index.params.GRAPH_STRUCTURAL_RELATIONSHIP_PATTERNS
NEO4J_RELATIONSHIP_PROPERTIES = rag.source.indexes.neo4j_index.params.GRAPH_RELATIONSHIP_COMMON_PROPERTIES
NEO4J_OPTIONAL_RELATIONSHIP_PROPERTIES = rag.source.indexes.neo4j_index.params.GRAPH_RELATIONSHIP_OPTIONAL_PROPERTIES
NEO4J_MAX_QUERY_LIMIT = 100

ES_MAX_SIZE = 200
QDRANT_DEFAULT_LIMIT = 20
QDRANT_READ_OPERATIONS = ("search", "scroll", "query_points", "count")
QDRANT_MAX_QUERY_LIMIT = 200
MAX_QDRANT_QUERY_TEXT_CHARS = 4_000

CYPHER_WRITE_KEYWORDS = (
    "create",
    "merge",
    "delete",
    "set",
    "remove",
    "drop",
    "load",
    "foreach",
    "detach",
)
