"""ycs/retriever — structured-output Pydantic for the entity-extraction LLM call.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntities(BaseModel):
    """Entity names identified in a user query (for graph retrieval)."""
    entities: list[str] = Field(
        description = (
            "Every entity name or meaningful term from the query "
            "(people, organizations, channels, works, places, topics, "
            "technologies, concepts, dates, numbers). When in doubt, "
            "include it. Empty list only when the query names nothing at all."
        ),
    )
