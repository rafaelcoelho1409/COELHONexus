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

GRAPH_NODE_COMMON_PROPERTIES = (
    ("id", "String"),
    ("kind", "String"),
    ("name", "String"),
    ("book_id", "String"),
    ("agentic_run_id", "String"),
    ("processed_book_revision", "Integer"),
)

GRAPH_NODE_PROPERTIES_BY_KIND = {
    "book": (),
    "section": (("section_path", "String"),),
    "source_page": (
        ("processed_page_number", "Integer"),
        ("source_page_number", "Integer"),
        ("content_role", "String"),
        ("content_sha256", "String"),
    ),
    "entity": (
        ("entity_type", "String"),
        ("page_numbers", "String"),
        ("citations", "String"),
    ),
    "relationship_evidence": (
        ("relation_kind", "String"),
        ("source_entity_id", "String"),
        ("target_entity_id", "String"),
        ("processed_page_number", "Integer"),
        ("source_page_number", "Integer"),
        ("quote", "String"),
    ),
}

GRAPH_STRUCTURAL_RELATIONSHIP_TYPES_BY_KIND = {
    "contains_section": "BD_CONTAINS",
    "contains_page": "BD_CONTAINS",
    "contains_entity": "BD_CONTAINS",
    "supported_by": "BD_SUPPORTED_BY",
    "has_evidence": "BD_HAS_EVIDENCE",
    "evidence_target": "BD_EVIDENCE_TARGET",
}

GRAPH_STRUCTURAL_RELATIONSHIP_PATTERNS = (
    (("Book", "Section"), "BD_CONTAINS", ("Section",)),
    (("Section",), "BD_CONTAINS", ("Section",)),
    (("Section",), "BD_CONTAINS", ("ProcessedPage",)),
    (("Book", "Section"), "BD_CONTAINS", ("Entity",)),
    (("Entity",), "BD_SUPPORTED_BY", ("ProcessedPage",)),
    (("Entity",), "BD_HAS_EVIDENCE", ("GraphEvidence",)),
    (("GraphEvidence",), "BD_EVIDENCE_TARGET", ("Entity",)),
    (("GraphEvidence",), "BD_SUPPORTED_BY", ("ProcessedPage",)),
    (("Entity",), "BD_REL_<predicate>", ("Entity",)),
    (("BD",), "BD_RELATES", ("BD",)),
)

GRAPH_RELATIONSHIP_COMMON_PROPERTIES = (
    ("id", "String"),
    ("kind", "String"),
    ("book_id", "String"),
    ("agentic_run_id", "String"),
    ("processed_book_revision", "Integer"),
)

GRAPH_RELATIONSHIP_OPTIONAL_PROPERTIES = (
    ("processed_page_numbers", "List<Integer>"),
    ("source_page_numbers", "List<Integer>"),
    ("citations", "String"),
    ("evidence_ids", "List<String>"),
    ("evidence", "List<String>"),
    ("model_properties", "String"),
)

GRAPH_FALLBACK_RELATIONSHIP_TYPE = "BD_RELATES"
GRAPH_MAX_SEMANTIC_RELATIONSHIP_TYPE_LENGTH = 100
