"""ycs/rag/standard/nodes/hallucination — grounding-judge prompt + version.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


HALLUCINATION_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


HALLUCINATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a hallucination detector. Given an answer and the source "
        "documents it was generated from, determine:\n"
        "1. Is the answer GROUNDED in the documents? (no fabricated facts)\n"
        "2. Does the answer ADDRESS the original question?\n"
        "Be strict. If the answer contains ANY claim not supported by the "
        "documents, mark it as not grounded.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Answer: {generation}\n\n"
        "Source documents:\n{documents}\n\n"
        "Evaluate the answer.",
    ),
])
