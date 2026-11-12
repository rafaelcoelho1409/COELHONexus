"""Structured contracts exchanged by YCS ingestion agents."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GraphEntityCandidate(BaseModel):
    """One transcript-grounded entity proposed for the knowledge graph."""

    model_config = ConfigDict(extra = "forbid")

    entity_id: str = Field(min_length = 1, max_length = 300)
    entity_type: str = Field(min_length = 1, max_length = 100)
    evidence: str = Field(min_length = 1, max_length = 800)


class GraphRelationshipCandidate(BaseModel):
    """One transcript-grounded relation between two proposed entities."""

    model_config = ConfigDict(extra = "forbid")

    source_id: str = Field(min_length = 1, max_length = 300)
    target_id: str = Field(min_length = 1, max_length = 300)
    relationship_type: str = Field(min_length = 1, max_length = 100)
    evidence: str = Field(min_length = 1, max_length = 800)


class GraphExtraction(BaseModel):
    """Bounded entity and relationship candidates for one video transcript."""

    model_config = ConfigDict(extra = "forbid")

    entities: list[GraphEntityCandidate] = Field(max_length = 150)
    relationships: list[GraphRelationshipCandidate] = Field(max_length = 300)
