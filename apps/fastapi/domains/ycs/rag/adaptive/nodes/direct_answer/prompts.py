"""ycs/rag/adaptive/nodes/direct_answer — FAST-path zero-retrieval prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L103-111`,
extended 2026-06-14 with a `MessagesPlaceholder("history")` slot so a
FAST-mode follow-up can build on prior turns the same way STANDARD's
generate does — see `nodes/generate/prompts.py` for the rationale."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


DIRECT_ANSWER_PROMPT_VERSION = "history-2026-06-14"


DIRECT_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant. Answer the user's question "
        "concisely from your general knowledge. If you are uncertain or "
        "the question requires specific video transcript evidence, say "
        "so clearly. Prior conversation turns (if any) are provided "
        "below — use them to resolve follow-ups like 'tell me more' or "
        "'expand on that', but stay honest about what you actually know.",
    ),
    MessagesPlaceholder("history"),
    ("human", "{question}"),
])
