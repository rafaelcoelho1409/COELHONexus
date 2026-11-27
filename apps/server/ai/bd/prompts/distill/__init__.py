"""Versioned prompts for Book Distiller learning-guide roles."""
from __future__ import annotations

from . import (
    book_synthesizer,
    chapter_synthesizer,
    domain,
    decision_brief_writer,
    elasticsearch_retriever,
    evidence_verifier,
    learning_extractor,
    neo4j_retriever,
    qdrant_retriever,
    report_reviewer,
    research_analyst,
    visual_artifact_builder,
    web_search_retriever,
)

__all__ = [
    "book_synthesizer",
    "chapter_synthesizer",
    "domain",
    "decision_brief_writer",
    "elasticsearch_retriever",
    "evidence_verifier",
    "learning_extractor",
    "neo4j_retriever",
    "qdrant_retriever",
    "report_reviewer",
    "research_analyst",
    "visual_artifact_builder",
    "web_search_retriever",
]
