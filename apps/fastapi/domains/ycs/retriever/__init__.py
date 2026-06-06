"""ycs/retriever — ES + Qdrant hybrid + Neo4j + SmartRetriever orchestrator.

Direct port of deprecated `services/youtube/retriever.py`.
All four retrievers share the same `retrieve(query, channel_ids)`
interface so the SmartRetriever fans out uniformly."""
from .elasticsearch import ElasticsearchRetriever
from .neo4j import Neo4jRetriever
from .params import (
    ES_DEFAULT_TOP_K,
    NEO4J_DEFAULT_TOP_K,
    QDRANT_DEFAULT_TOP_K,
    SMART_DEFAULT_TOP_K,
)
from .prompts import ENTITY_EXTRACTION_PROMPT, RETRIEVER_PROMPT_VERSION
from .qdrant_hybrid import QdrantHybridRetriever
from .schemas import ExtractedEntities
from .smart import SmartRetriever


__all__ = [
    "ENTITY_EXTRACTION_PROMPT",
    "ES_DEFAULT_TOP_K",
    "ElasticsearchRetriever",
    "ExtractedEntities",
    "NEO4J_DEFAULT_TOP_K",
    "Neo4jRetriever",
    "QDRANT_DEFAULT_TOP_K",
    "QdrantHybridRetriever",
    "RETRIEVER_PROMPT_VERSION",
    "SMART_DEFAULT_TOP_K",
    "SmartRetriever",
]
