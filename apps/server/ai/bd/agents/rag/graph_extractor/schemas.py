"""Structured output contracts for book knowledge graph extraction."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace = True, min_length = 1)]


class GraphEntityCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    entity_id: NonEmptyText = Field(max_length = 240)
    entity_type: Literal[
        "concept", "method", "person", "organization", "technology", "principle", "event", "metric", "other",
    ]
    evidence: NonEmptyText = Field(max_length = 800)


class GraphRelationshipCandidate(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    source_id: NonEmptyText = Field(max_length = 240)
    target_id: NonEmptyText = Field(max_length = 240)
    relationship_type: Literal[
        "uses", "depends_on", "causes", "supports", "contrasts_with", "part_of",
        "example_of", "measured_by", "leads_to", "related_to",
    ]
    evidence: NonEmptyText = Field(max_length = 800)


class GraphExtraction(BaseModel):
    model_config = ConfigDict(extra = "forbid")

    entities: list[GraphEntityCandidate] = Field(max_length = 60)
    relationships: list[GraphRelationshipCandidate] = Field(max_length = 100)
