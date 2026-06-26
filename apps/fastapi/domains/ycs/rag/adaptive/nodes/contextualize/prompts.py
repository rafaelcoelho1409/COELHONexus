"""ycs/rag/adaptive/nodes/contextualize — standalone-question rewriter prompt.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


CONTEXTUALIZE_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


CONTEXTUALIZE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a question contextualizer. Given a conversation history "
        "and a new question, determine if the question references "
        "previous context (pronouns like 'she', 'he', 'that', 'it', "
        "'they', phrases like 'tell me more', 'what about', 'the same', "
        "'and what about', or any implicit references to prior topics).\n\n"
        "If YES: Rewrite the question as a standalone question that "
        "includes the necessary context.\n"
        "If NO: Return the original question unchanged.\n\n"
        "Return ONLY the rewritten question. Nothing else.",
    ),
    (
        "human",
        "Conversation history:\n{history}\n\nNew question: {question}",
    ),
])
