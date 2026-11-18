"""Stable structural labels and relationship types for the BD graph projection."""
from __future__ import annotations

GRAPH_BATCH_CHARS = 18000
GRAPH_WRITE_BATCH_SIZE = 500

GRAPH_NODE_LABELS_BY_KIND = {
    "book": "Book",
    "section": "Section",
    "source_page": "ProcessedPage",
    "entity": "Entity",
    "relationship_evidence": "GraphEvidence",
}

GRAPH_STRUCTURAL_RELATIONSHIP_TYPES_BY_KIND = {
    "contains_section": "BD_CONTAINS",
    "contains_page": "BD_CONTAINS",
    "contains_entity": "BD_CONTAINS",
    "supported_by": "BD_SUPPORTED_BY",
    "has_evidence": "BD_HAS_EVIDENCE",
    "evidence_target": "BD_EVIDENCE_TARGET",
}

GRAPH_FALLBACK_RELATIONSHIP_TYPE = "BD_RELATES"
GRAPH_MAX_SEMANTIC_RELATIONSHIP_TYPE_LENGTH = 100
