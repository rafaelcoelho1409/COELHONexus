"""ycs/rag/standard — STANDARD RAG pipeline (the deprecated `YouTubeContentGraph`).

Direct port of deprecated `graphs/youtube/rag.py`. Exposed for the
adaptive parent graph to wire as a sub-pipeline (STANDARD mode +
DEEP sub-agents)."""
from .graph import build_youtube_rag_graph
from .params import DEFAULT_MAX_RETRIES, DEFAULT_RECURSION_LIMIT
from .state import YouTubeRAGState


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_RECURSION_LIMIT",
    "YouTubeRAGState",
    "build_youtube_rag_graph",
]
