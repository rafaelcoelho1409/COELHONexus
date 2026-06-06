"""ycs/rag/standard/nodes/generate — answer-generation prompt + version.

Direct port of deprecated `schemas/youtube/prompts.py:L7-21`."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


GENERATE_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


GENERATE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant that answers questions about YouTube "
        "video content. Use ONLY the provided transcript excerpts to "
        "answer. Always cite your sources using this format: [Video: title] "
        "If the transcripts don't contain enough information, say so clearly.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Video transcripts:\n{context}\n\n"
        "Answer the question based on the transcripts above. Include citations.",
    ),
])
