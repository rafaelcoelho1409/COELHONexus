"""Validated query intent accepted by the BD graph retrieval boundary."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


QueryTerm = Annotated[str, Field(min_length = 2, max_length = 120)]


class Neo4jQueryIntent(BaseModel):
    """Bounded graph search hints; this never accepts Cypher or query syntax."""

    model_config = ConfigDict(extra = "forbid", str_strip_whitespace = True)

    entity_terms: list[QueryTerm] = Field(
        default_factory = list,
        max_length = 8,
        description = "Entity names or concepts to prioritize in the selected book graph.",
    )
    entity_types: list[QueryTerm] = Field(
        default_factory = list,
        max_length = 8,
        description = "Optional exact entity_type values; matching is case-insensitive.",
    )
    relationship_terms: list[QueryTerm] = Field(
        default_factory = list,
        max_length = 8,
        description = "Relationship kinds or predicates to prioritize.",
    )
    relationship_direction: Literal["either", "outgoing", "incoming"] = Field(
        default = "either",
        description = "Direction relative to a matched entity when selecting related graph edges.",
    )
