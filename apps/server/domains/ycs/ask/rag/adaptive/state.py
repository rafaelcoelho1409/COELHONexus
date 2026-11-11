"""Compatibility state for the YCS adaptive graph's legacy callers.

The application-owned LangGraph state is defined in ``ai.ycs``; this
shape remains for existing domain nodes and compatibility imports.
"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AdaptiveRAGState(TypedDict):
    """State for the Adaptive RAG parent graph."""
    # Shared fields
    question:           str
    contextualized:     bool
    intent:             str               # "corpus" or corpus-free "conversation"
    mode:               str               # "fast" | "standard" | "deep"
    force_mode:         str               # Optional override from the API
    generation:         str               # Final answer
    citations:          list[dict]        # Final citations
    grounded:           bool              # Grounding status
    retrieval_sources:  list[str]
    retry_count:        int               # Preserved for response compat
    search_query:       str               # Preserved for response compat
    # Conversation memory — previous Q&A pairs for follow-up context
    conversation_history: list[dict]
    # Scope filter — auto-detected from question or manual override
    channel_ids:        list[str]
    # DEEP mode fields
    sub_questions:      list[str]
    sub_results:        Annotated[list[dict], operator.add]
    research_plan:      str
    confidence_score:   float
