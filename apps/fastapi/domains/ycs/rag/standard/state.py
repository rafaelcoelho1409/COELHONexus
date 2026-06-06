"""ycs/rag/standard — LangGraph state for the STANDARD pipeline.

Direct port of deprecated `schemas/youtube/state.py:L17-27`. The
TypedDict shape is the contract between nodes — LangGraph merges
each node's returned partial state into this."""
from __future__ import annotations

from typing import TypedDict

from langchain_core.documents import Document


class YouTubeRAGState(TypedDict):
    """State for the STANDARD retrieval pipeline."""
    question:           str               # User's original question
    documents:          list[Document]    # Retrieved and graded documents
    generation:         str               # Generated answer
    retry_count:        int               # Number of rewrite-retrieve cycles
    search_query:       str               # Current search query (rewritten or original)
    grounded:           bool              # Is generation grounded in documents?
    citations:          list[dict]        # Formatted citations with video title + URL
    retrieval_sources:  list[str]         # Which retrievers found documents
