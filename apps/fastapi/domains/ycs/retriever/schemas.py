"""ycs/retriever — structured-output Pydantic for the entity-extraction LLM call.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntities(BaseModel):
    """Entity names identified in a user query (for graph retrieval)."""
    entities: list[str] = Field(
        description = (
            "List of NAMED entity names (people, organizations, channels, "
            "works, places, terms of art) mentioned in the query. Never "
            "common nouns or meta-words (numbers, dates, videos, sources). "
            "Empty list when the query names nothing specific."
        ),
    )
