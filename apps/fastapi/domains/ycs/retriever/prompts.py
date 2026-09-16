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
        # 2026-09-16: constrained to NAMED entities. The retriever matches
        # these against graph node ids with exact-then-substring lookup —
        # a common noun ("numbers", "dates", "videos", "sources") can never
        # equal a node id, so emitting one only burns an LLM call and a
        # graph lookup for a guaranteed empty tier (observed live: every
        # generic term returned 0 docs). When in doubt, emit fewer, more
        # specific names rather than more, vaguer ones; an empty list is a
        # valid answer and simply yields to the vector/full-text arms.
        # Graph content is Brazilian Portuguese: prefer the PT surface form
        # ("reforma tributária" over "tax reform", "Brasil" over "Brazil")
        # so extracted names actually coincide with stored node ids.
        "Extract NAMED entity names from the user's question: people, "
        "organizations, channels, works, places, and specific terms of art. "
        "Do NOT emit common nouns, generic topics, or meta-words about the "
        "question itself (never: numbers, dates, videos, sources, details, "
        "claims, conclusions). "
        "Return only the entity names as a list. Be concise.",
    ),
    ("human", "{query}"),
])
