"""ycs/graph_builder — LLMGraphTransformer + rapidfuzz + semantic
entity resolution + Neo4j writes.
g., `Astronomia`↔`Gastronomia`).
Threshold 0.85, empirically tuned against `baai/bge-m3`'s score
distribution back when entity-resolution pinned that model specifically.
2026-09-13: now shares the same embedding-endpoint singleton as the main
Qdrant path (no more per-call model pinning — see
`service.py::_embed_ids_for_resolution`), so this threshold may need
re-tuning against whatever the endpoint currently resolves to. Schema-free
(NO `allowed_nodes` constraint) with formatting-only LLM guidance — works
across any YouTube topic."""
from .params import (
    DEFAULT_BATCH_SIZE,
    EMBED_COSINE_CUTOFF,
    EXTRACT_CONCURRENCY,
    FUZZ_MERGE_CUTOFF,
    NUMERIC_LABELS_SKIP,
)
from .prompts import EXTRACTION_INSTRUCTIONS, SCHEMA_DISCOVERY_PROMPT
from .schemas import SchemaDiscovery
from .service import (
    build_video_metadata_graph,
    create_graph_transformer,
    delete_documents_for_videos,
    discover_schema,
    extract_and_store_graph,
    get_graph_stats,
    resolve_entities,
)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "EMBED_COSINE_CUTOFF",
    "EXTRACTION_INSTRUCTIONS",
    "EXTRACT_CONCURRENCY",
    "FUZZ_MERGE_CUTOFF",
    "NUMERIC_LABELS_SKIP",
    "SCHEMA_DISCOVERY_PROMPT",
    "SchemaDiscovery",
    "build_video_metadata_graph",
    "create_graph_transformer",
    "delete_documents_for_videos",
    "discover_schema",
    "extract_and_store_graph",
    "get_graph_stats",
    "resolve_entities",
]
