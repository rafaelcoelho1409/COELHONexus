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
        # 2026-09-16 (rev.2): deliberately UNRESTRICTED — emit every
        # potentially relevant name or term from the question: people,
        # organizations, channels, works, places, topics, technologies,
        # concepts, dates, numbers, and meta-terms alike. Recall over
        # precision here: the retriever tries exact match first and only
        # then a substring fallback, and extra candidates that match
        # nothing simply yield no docs — they never corrupt results.
        # Graph content is Brazilian Portuguese: include the PT surface
        # form alongside the EN one whenever both exist ("reforma
        # tributária" + "tax reform", "Brasil" + "Brazil") so matching
        # hits regardless of the language the question was asked in.
        "Extract all entity names and meaningful terms from the user's "
        "question: people, organizations, channels, works, places, "
        "topics, technologies, concepts, dates, numbers — anything that "
        "could identify relevant content. When in doubt, include it; "
        "more candidates are better than fewer. "
        "Return only the names as a list. Be thorough.",
    ),
    ("human", "{query}"),
])
