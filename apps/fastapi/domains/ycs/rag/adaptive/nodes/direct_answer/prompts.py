"""ycs/rag/adaptive/nodes/direct_answer — FAST-path zero-retrieval prompt.
py` for the rationale.

2026-06-16 — chat-style output: 2-5 sentences total, conversational
tone, no headers. Matches the YCS Ask UI's chat container and the
"factual quick answer" purpose of FAST mode."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


DIRECT_ANSWER_PROMPT_VERSION = "chat-2026-06-16"


DIRECT_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant inside a CHAT interface. Answer "
        "the user's question in a conversational tone, drawing on your "
        "general knowledge. Compose your reply in this exact shape:\n\n"
        "1. **First sentence answers the question directly.** No "
        "preamble.\n"
        "2. **1–4 additional sentences** if context, qualification, or "
        "examples genuinely help. Otherwise stop at one sentence.\n"
        "3. **Total length: 2–5 sentences.** This is FAST mode — "
        "brevity is the feature.\n\n"
        "Hard rules:\n"
        "- No `## Section` headers, no bullet lists, no markdown "
        "tables — this is conversational prose.\n"
        "- No 'I am an AI' / 'as a language model' preface.\n"
        "- If the question requires specific video transcript evidence "
        "you don't have, say so in one sentence and suggest switching "
        "to STANDARD or DEEP mode.\n\n"
        "Prior conversation turns (if any) are provided below — use "
        "them to resolve follow-ups like 'tell me more' or 'expand on "
        "that', but stay honest about what you actually know.",
    ),
    MessagesPlaceholder("history"),
    ("human", "{question}"),
])
