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
from __future__ import annotations

from . import domain, params, prompts, schemas, service
