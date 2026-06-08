"""ycs/graph_builder — LLMGraphTransformer + rapidfuzz + BGE-M3 semantic
entity resolution + Neo4j writes.

Direct port of deprecated `services/youtube/graph_builder.py` with one
deviation: Step 3 entity resolution adds a BGE-M3 embedding-cosine
gate after the rapidfuzz pre-filter to kill character-similar /
semantically-different merges (e.g., `Astronomia`↔`Gastronomia`).
Threshold 0.85, empirically tuned (see `params.RESOLVE_EMBED_MODEL`
docstring). Schema-free (NO `allowed_nodes` constraint) with
formatting-only LLM guidance — works across any YouTube topic."""
from .params import (
    DEFAULT_BATCH_SIZE,
    EMBED_COSINE_CUTOFF,
    FUZZ_MERGE_CUTOFF,
    INTER_BATCH_SLEEP_S,
    NUMERIC_LABELS_SKIP,
    RESOLVE_EMBED_MODEL,
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
    "FUZZ_MERGE_CUTOFF",
    "INTER_BATCH_SLEEP_S",
    "NUMERIC_LABELS_SKIP",
    "RESOLVE_EMBED_MODEL",
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
