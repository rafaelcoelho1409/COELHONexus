"""ycs/rag/standard — LangGraph state for the STANDARD pipeline.
The
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
    # Prior Q+A pairs (oldest-first). Threaded down from `AdaptiveRAGState`
    # by `run_standard` so the `generate` node sees the multi-turn context
    # — sub-agents (DEEP fan-out) receive [] because their sub-question is
    # already self-contained.
    conversation_history: list[dict]
    # pre-grade soft evidence. Accumulates docs the
    # retriever (Neo4j graph + Qdrant hybrid) returned BEFORE the
    # grader filtered them, deduped + capped across all rewrite
    # rounds. Consumed only by `fallback_answer` when the grader
    # rejected every candidate — those rejected docs are still the
    # closest matches the corpus has, and CRAG-style graceful
    # degradation should use them as soft hints + surface them as
    # related-video citations rather than throw them away.
    # The `documents` field above keeps its grader-filtered semantics
    # for the strict-evidence `generate` path; this is the parallel
    # channel for the rescue path.
    pre_grade_documents: list[Document]
