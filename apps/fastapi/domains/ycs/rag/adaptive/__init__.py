"""ycs/rag/adaptive — Adaptive RAG parent graph (FAST/STANDARD/DEEP).

Wraps `domains/ycs/rag/standard` as a sub-graph + adds the FAST
(direct answer) and DEEP (planner → subagents → synthesize → critic)
paths. Direct port of deprecated `graphs/youtube/adaptive.py`."""
from .graph import build_adaptive_rag_graph
from .params import (
    CRITIC_FALLBACK_CONFIDENCE,
    MAX_HISTORY_ANSWER_CHARS,
    MAX_HISTORY_TURNS,
    SUBGRAPH_RECURSION_LIMIT,
)
from .state import AdaptiveRAGState


__all__ = [
    "AdaptiveRAGState",
    "CRITIC_FALLBACK_CONFIDENCE",
    "MAX_HISTORY_ANSWER_CHARS",
    "MAX_HISTORY_TURNS",
    "SUBGRAPH_RECURSION_LIMIT",
    "build_adaptive_rag_graph",
]
