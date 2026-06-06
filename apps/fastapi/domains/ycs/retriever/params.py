"""ycs/retriever — top_k defaults shared across the four retrievers.

Mirror of deprecated `services/youtube/retriever.py` defaults:
  ElasticsearchRetriever  → 10 (`L47`)
  QdrantHybridRetriever   → 10 (`L138`)
  Neo4jRetriever          → 10 (`L259`)
  SmartRetriever          → 10 (`L436`)

The Qdrant + Neo4j paths over-fetch internally (Qdrant: `top_k * 2`
prefetch per vector; Neo4j: `top_k * 2` LIMIT in the UNION) so the
fusion / dedup step has headroom to reorder before truncating."""
from __future__ import annotations


ES_DEFAULT_TOP_K = 10
QDRANT_DEFAULT_TOP_K = 10
NEO4J_DEFAULT_TOP_K = 10
SMART_DEFAULT_TOP_K = 10
