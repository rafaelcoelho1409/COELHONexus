"""
LangGraph State Definitions for Agentic RAG

CONCEPT: LangGraph state is a TypedDict that flows between nodes.
Each node receives the full state and returns a dict with the keys it wants to update.
LangGraph merges the returned dict into the state automatically.

The state schema defines ALL data channels in the graph:
- question: what the user asked
- documents: retrieved + graded documents
- generation: the LLM's answer
- retry_count: how many rewrite-retrieve cycles have happened
- search_query: the current search query (starts as question, may diverge after rewriting)
- grounded: whether the generation is grounded in the documents (Phase 4)
- citations: formatted source citations (Phase 4)
- retrieval_sources: which retrievers contributed documents (Phase 4)
"""
from typing import TypedDict
from langchain_core.documents import Document


class YouTubeRAGState(TypedDict):
    question: str               # User's original question
    documents: list[Document]   # Retrieved and graded documents
    generation: str             # Generated answer
    retry_count: int            # Number of rewrite-retrieve cycles
    search_query: str           # Current search query (rewritten or original)
    # Phase 4: Production hardening fields
    grounded: bool              # Is generation grounded in documents?
    citations: list[dict]       # Formatted citations with video title + URL
    retrieval_sources: list[str]  # Which retrievers found documents
