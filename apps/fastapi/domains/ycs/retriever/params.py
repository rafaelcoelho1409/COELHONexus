"""ycs/retriever — top_k defaults shared across the four retrievers."""
from __future__ import annotations


ES_DEFAULT_TOP_K = 10
QDRANT_DEFAULT_TOP_K = 10
NEO4J_DEFAULT_TOP_K = 10
SMART_DEFAULT_TOP_K = 10

# 2026-09-16: entity-inventory hint for the Neo4j extractor
# (`retriever/neo4j.py::fetch_entity_inventory`). The inventory feeds the
# extraction prompt with the graph's real surface forms so the LLM emits
# names that actually exist instead of guessing spellings.
# 500 ids ≈ a few KB of prompt — negligible next to a transcript-backed
# call, and the graph is small enough that truncation rarely bites; when
# it does, the exact->CONTAINS tiers still catch the rest. 10-min TTL:
# fresh enough after ingestion runs, cheap enough per query (one indexed
# scan per worker per 10 min, not per request).
INVENTORY_MAX_IDS = 500
INVENTORY_TTL_S = 600
