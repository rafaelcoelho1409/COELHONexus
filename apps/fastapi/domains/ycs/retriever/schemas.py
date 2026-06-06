"""ycs/retriever — structured-output Pydantic for the entity-extraction LLM call.

Direct port of deprecated `schemas/youtube/agents.py:L76-80`."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ExtractedEntities(BaseModel):
    """Entity names identified in a user query (for graph retrieval)."""
    entities: list[str] = Field(
        description = (
            "List of entity names (people, topics, technologies, channels) "
            "mentioned in the query"
        ),
    )
