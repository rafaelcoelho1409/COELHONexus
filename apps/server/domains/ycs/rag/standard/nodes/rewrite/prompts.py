"""ycs/rag/standard/nodes/rewrite — query-rewrite prompt + version.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


REWRITE_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query rewriter. The original query did not return "
        "relevant results. Rewrite it to be more specific or use "
        "different terms that might match video transcripts. Return "
        "ONLY the rewritten query, nothing else.",
    ),
    (
        "human",
        "Original question: {question}\n"
        "Previous search query: {search_query}\n"
        "Rewrite this as a better search query:",
    ),
])
