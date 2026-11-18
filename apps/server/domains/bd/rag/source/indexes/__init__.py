"""Database projections for processed-book retrieval."""
from . import elasticsearch_index, neo4j_index, qdrant_index

__all__ = ["elasticsearch_index", "neo4j_index", "qdrant_index"]
