"""Structured output contracts for book knowledge graph extraction."""
from __future__ import annotations

import typing

import pydantic


NonEmptyText = typing.Annotated[str, pydantic.StringConstraints(strip_whitespace = True, min_length = 1)]


class GraphEntityCandidate(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    entity_id: NonEmptyText = pydantic.Field(max_length = 240)
    entity_type: typing.Literal[
        "concept", "method", "person", "organization", "technology", "principle", "event", "metric", "other",
    ]
    evidence: NonEmptyText = pydantic.Field(max_length = 800)


class GraphRelationshipCandidate(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    source_id: NonEmptyText = pydantic.Field(max_length = 240)
    target_id: NonEmptyText = pydantic.Field(max_length = 240)
    relationship_type: typing.Literal[
        "uses", "depends_on", "causes", "supports", "contrasts_with", "part_of",
        "example_of", "measured_by", "leads_to", "related_to",
    ]
    evidence: NonEmptyText = pydantic.Field(max_length = 800)


class GraphExtraction(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra = "forbid")

    entities: list[GraphEntityCandidate] = pydantic.Field(max_length = 60)
    relationships: list[GraphRelationshipCandidate] = pydantic.Field(max_length = 100)
