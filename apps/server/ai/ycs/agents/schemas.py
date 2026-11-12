"""Structured contracts exchanged by YCS ingestion agents."""
from __future__ import annotations

import pydantic


class GraphEntityCandidate(pydantic.BaseModel):
    """One transcript-grounded entity proposed for the knowledge graph."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    entity_id: str = pydantic.Field(min_length = 1, max_length = 300)
    entity_type: str = pydantic.Field(min_length = 1, max_length = 100)
    evidence: str = pydantic.Field(min_length = 1, max_length = 800)


class GraphRelationshipCandidate(pydantic.BaseModel):
    """One transcript-grounded relation between two proposed entities."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    source_id: str = pydantic.Field(min_length = 1, max_length = 300)
    target_id: str = pydantic.Field(min_length = 1, max_length = 300)
    relationship_type: str = pydantic.Field(min_length = 1, max_length = 100)
    evidence: str = pydantic.Field(min_length = 1, max_length = 800)


class GraphExtraction(pydantic.BaseModel):
    """Bounded entity and relationship candidates for one video transcript."""

    model_config = pydantic.ConfigDict(extra = "forbid")

    entities: list[GraphEntityCandidate] = pydantic.Field(max_length = 150)
    relationships: list[GraphRelationshipCandidate] = pydantic.Field(max_length = 300)
