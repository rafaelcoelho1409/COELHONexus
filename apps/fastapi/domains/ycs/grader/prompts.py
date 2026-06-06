"""ycs/grader — relevance-grading prompt + version marker.

Direct port of deprecated `schemas/youtube/prompts.py:L154-167`. Version
string is here per `docs/CODE-CONVENTIONS.md` §2: cache-invalidation
knobs live with the prompts they identify."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


GRADER_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


GRADING_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a relevance grader. Given a user question and a "
        "retrieved document, determine if the document contains "
        "information relevant to answering the question. Respond with "
        "'relevant' or 'not_relevant'. A document is relevant if it "
        "contains ANY information that helps answer the question, "
        "even partially.",
    ),
    (
        "human",
        "Question: {question}\n\nDocument content:\n{document}",
    ),
])
