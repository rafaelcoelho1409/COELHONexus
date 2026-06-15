"""ycs/rag/standard/nodes/generate — answer-generation prompt + version.

Direct port of deprecated `schemas/youtube/prompts.py:L7-21`, extended
2026-06-14 with a `MessagesPlaceholder("history")` slot so the
generator sees prior conversation turns when producing a follow-up.

Why: without history the LLM re-derives every answer in isolation —
"compare those" / "tell me more" / "skip the offshore part" carry no
weight. With history, the model can reference its own prior wording,
stay stylistically consistent, and build on (not repeat) prior turns.
The `contextualize` node still runs FIRST and rewrites the question
into a standalone form for retrieval; history is what lets the
generator polish the answer beyond what retrieval alone provides."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


GENERATE_PROMPT_VERSION = "history-2026-06-14"


GENERATE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant that answers questions about YouTube "
        "video content. Use ONLY the provided transcript excerpts to "
        "answer. Always cite your sources using this format: [Video: title] "
        "If the transcripts don't contain enough information, say so clearly."
        "\n\n"
        "Prior conversation turns (if any) are provided below. Treat them "
        "as authoritative context for follow-up questions like 'tell me "
        "more', 'compare those', or 'what about X' — but never invent "
        "transcript content from them; ground every NEW factual claim in "
        "the transcripts attached to THIS turn.",
    ),
    MessagesPlaceholder("history"),
    (
        "human",
        "Question: {question}\n\n"
        "Video transcripts:\n{context}\n\n"
        "Answer the question based on the transcripts above. Include citations.",
    ),
])
