"""ycs/query — cross-store query service powering the YCS Query page.

Two surface layers:
  · Free-text search  — query_es / query_qdrant / query_neo4j
                        (legacy `q` style for the original empty-page UX)
  · Raw DSL workbench — raw_es / raw_qdrant / raw_neo4j
                        (CodeMirror-driven Phase 1 of the SOTA workbench)

The app→backend support matrix lives in `params.APP_BACKENDS` —
single source of truth for both the service guards and the UI's
grey-out behavior."""
from .params import (
    APP_BACKENDS,
    APPS,
    BACKEND_ES,
    BACKEND_NEO4J,
    BACKEND_QDRANT,
    BACKENDS,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    is_supported,
    namespace_label,
)
from .safety import (
    QueryNotAllowed,
    assert_cypher_readonly,
    parse_es_body,
    parse_qdrant_body,
)
from .schemas import (
    AIGenerateRequest,
    HistoryEntry,
    HistoryList,
    HistorySaveRequest,
    NamespaceMap,
    QueryHit,
    QueryRequest,
    QueryResponse,
    RawQueryHit,
    RawQueryRequest,
    RawQueryResponse,
    SchemaResponse,
)
from .service import (
    query_es,
    query_neo4j,
    query_qdrant,
    raw_es,
    raw_neo4j,
    raw_qdrant,
)


__all__ = [
    "AIGenerateRequest",
    "APP_BACKENDS",
    "APPS",
    "BACKEND_ES",
    "BACKEND_NEO4J",
    "BACKEND_QDRANT",
    "BACKENDS",
    "DEFAULT_LIMIT",
    "HistoryEntry",
    "HistoryList",
    "HistorySaveRequest",
    "MAX_LIMIT",
    "NamespaceMap",
    "QueryHit",
    "QueryNotAllowed",
    "QueryRequest",
    "QueryResponse",
    "RawQueryHit",
    "RawQueryRequest",
    "RawQueryResponse",
    "SchemaResponse",
    "assert_cypher_readonly",
    "is_supported",
    "namespace_label",
    "parse_es_body",
    "parse_qdrant_body",
    "query_es",
    "query_neo4j",
    "query_qdrant",
    "raw_es",
    "raw_neo4j",
    "raw_qdrant",
]
