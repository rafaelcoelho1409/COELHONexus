"""ycs/rag/adaptive/nodes/synthesize — DEEP-path merger prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L113-132`."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


SYNTHESIZE_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


SYNTHESIZE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research synthesizer. You receive the results of "
        "multiple parallel research sub-questions about the same "
        "overarching topic. Your job is to:\n"
        "1. Combine all findings into a coherent analytical report\n"
        "2. Cross-reference findings — identify patterns that emerge "
        "across sub-questions\n"
        "3. Note any contradictions or tensions between findings\n"
        "4. Structure the output clearly with sections\n"
        "5. Cite sources using [Video: title] format\n"
        "Do NOT fabricate information. Only synthesize what the "
        "sub-research found.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Research plan: {research_plan}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Synthesize these findings into a comprehensive analytical report.",
    ),
])
