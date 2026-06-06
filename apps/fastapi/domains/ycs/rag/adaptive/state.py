"""ycs/rag/adaptive — LangGraph state for the FAST/STANDARD/DEEP parent.

Wraps `YouTubeRAGState` fields + DEEP-mode accumulators. `sub_results`
uses `operator.add` so parallel sub-agents (via LangGraph `Send()`)
accumulate without overwriting.

Direct port of deprecated `schemas/youtube/state.py:L30-59`."""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class AdaptiveRAGState(TypedDict):
    """State for the Adaptive RAG parent graph."""
    # Shared fields
    question:           str
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
