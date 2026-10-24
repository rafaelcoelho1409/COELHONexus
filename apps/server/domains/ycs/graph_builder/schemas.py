"""ycs/graph_builder — structured-output Pydantic for schema discovery.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class SchemaDiscovery(BaseModel):
    """Auto-discovered knowledge-graph schema from sample transcripts."""
    allowed_nodes: list[str] = Field(
        description = "Entity types to extract (e.g., Country, Person, Organization)",
    )
    allowed_relationships: list[str] = Field(
        description = "Relationship types (e.g., RECOMMENDS, LOCATED_IN)",
    )
    extraction_focus: str = Field(
        description = "Brief description of what to focus on during extraction",
    )
