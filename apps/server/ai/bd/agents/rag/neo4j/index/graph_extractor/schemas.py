"""Compact, evidence-first schema for BD knowledge graph extraction."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


def edge(label: str, *, reference: bool = False, **kwargs: Any) -> Any:
    """Mark a Pydantic field as a Docling Graph relationship."""
    schema_extra = {"edge_label": label}
    if reference:
        schema_extra["graph_reference"] = True
    return Field(..., json_schema_extra=schema_extra, **kwargs)


class Entity(BaseModel):
    """A named person, organization, place, work, concept, or other source entity."""

    model_config = ConfigDict(
        graph_id_fields=["name", "entity_type"],
        graph_max_instances=150,
    )

    name: str = Field(
        description="Entity name as written in the source; do not infer an alias or expand a name.",
    )
    entity_type: str = Field(
        description="Short singular entity type, such as Person, Organization, Location, or Concept.",
    )


class Fact(BaseModel):
    """One explicit directed relation between two named entities, with its exact evidence."""

    model_config = ConfigDict(
        graph_id_fields=["source_name", "predicate", "target_name"],
        graph_max_instances=300,
    )

    source_name: str = Field(
        description="Exact source entity name copied from the supporting sentence.",
    )
    predicate: str = Field(
        description="Concise directed verb relation in UPPER_SNAKE_CASE, preserving polarity and attribution.",
    )
    target_name: str = Field(
        description="Exact target entity name copied from the supporting sentence.",
    )
    source: Entity = edge(
        "SOURCE",
        reference=True,
        description="Identity-only reference to the source entity.",
    )
    target: Entity = edge(
        "TARGET",
        reference=True,
        description="Identity-only reference to the target entity.",
    )
    evidence: str = Field(
        description="A short, exact source excerpt that names both endpoints and states this relation.",
    )

    @model_validator(mode="after")
    def align_identity_with_references(self) -> Fact:
        """Keep fact identity fields consistent with their canonical references."""
        self.source_name = self.source.name.strip()
        self.target_name = self.target.name.strip()
        self.predicate = self.predicate.strip()
        self.evidence = self.evidence.strip()
        return self


class KnowledgeGraph(BaseModel):
    """The extracted entities and source-grounded facts for one input document."""

    model_config = ConfigDict(graph_id_fields=["document_key"])

    document_key: str = Field(
        description="Keep this fixed value unchanged; it identifies the extraction root.",
    )
    entities: list[Entity] = edge(
        "HAS_ENTITY",
        default_factory=list,
        description="Named entities that participate in a supported fact.",
    )
    facts: list[Fact] = edge(
        "HAS_FACT",
        default_factory=list,
        description="Distinct, directed facts stated explicitly in the source.",
    )
