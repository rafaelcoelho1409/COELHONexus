"""ycs/rag/adaptive/nodes/direct_answer — FAST-path zero-retrieval prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L103-111`."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


DIRECT_ANSWER_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


DIRECT_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant. Answer the user's question "
        "concisely from your general knowledge. If you are uncertain or "
        "the question requires specific video transcript evidence, say "
        "so clearly.",
    ),
    ("human", "{question}"),
])
