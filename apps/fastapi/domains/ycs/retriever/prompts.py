"""ycs/retriever — entity-extraction prompt for the Neo4j retriever.
Lives under `retriever/` because it's owned by the Neo4j retriever and
nothing else uses it — keeping it co-located avoids a cross-module
import that would otherwise have to live in a `prompts/` package."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


RETRIEVER_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


ENTITY_EXTRACTION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "Extract entity names from the user's question. "
        "Entities are: people, topics, technologies, concepts, channels. "
        "Return only the entity names as a list. Be concise.",
    ),
    ("human", "{query}"),
])
