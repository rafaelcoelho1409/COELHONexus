"""
LangGraph State Definitions for Agentic RAG and Knowledge Distiller

CONCEPT: LangGraph state is a TypedDict that flows between nodes.
Each node receives the full state and returns a dict with the keys it wants to update.
LangGraph merges the returned dict into the state automatically.

State schemas:
- YouTubeRAGState: the STANDARD pipeline (retrieve → grade → generate → check)
- AdaptiveRAGState: the parent graph with FAST/STANDARD/DEEP routing
"""
import operator
from typing import Annotated, TypedDict, Optional, Literal
from langchain_core.documents import Document


class YouTubeRAGState(TypedDict):
    """State for the STANDARD retrieval pipeline (unchanged from Phase 4)."""
    question: str  # User's original question
    documents: list[Document]  # Retrieved and graded documents
    generation: str  # Generated answer
    retry_count: int  # Number of rewrite-retrieve cycles
    search_query: str  # Current search query (rewritten or original)
    # Phase 4: Production hardening fields
    grounded: bool  # Is generation grounded in documents?
    citations: list[dict]  # Formatted citations with video title + URL
    retrieval_sources: list[str]  # Which retrievers found documents


class AdaptiveRAGState(TypedDict):
    """
    State for the Adaptive RAG parent graph.

    CONCEPT: The parent graph wraps the STANDARD pipeline and adds two
    additional paths: FAST (direct answer) and DEEP (multi-agent research).
    A classifier routes each query to the best strategy.

    The sub_results field uses operator.add as reducer so that parallel
    subagents (via LangGraph Send()) accumulate results without overwriting.
    """
    # Shared fields
    question: str  # User's original question
    mode: str  # "fast" | "standard" | "deep"
    force_mode: str  # Optional override from API
    generation: str  # Final answer
    citations: list[dict]  # Final citations
    grounded: bool  # Grounding status
    retrieval_sources: list[str]  # Sources that contributed
    retry_count: int  # Preserved for response compat
    search_query: str  # Preserved for response compat
    # Conversation memory — previous Q&A pairs for follow-up context
    conversation_history: list[dict]  # [{question, answer}] from PostgreSQL
    # Scope filter — auto-detected from question or manual override
    channel_ids: list[str]  # Scope retrieval to these channels (empty = all)
    # DEEP mode fields
    sub_questions: list[str]  # Decomposed questions from planner
    sub_results: Annotated[list[dict], operator.add]  # Accumulated subagent results
    research_plan: str  # Planner's strategy
    confidence_score: float  # Critic's assessment (0-1)
