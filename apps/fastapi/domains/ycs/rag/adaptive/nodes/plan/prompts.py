"""ycs/rag/adaptive/nodes/plan — DEEP-path fallback research-planner prompt.

The primary planning path reuses `sub_questions` produced by the
classifier (deprecated `adaptive.py:L120, L192-195`). This prompt only
fires when the classifier didn't pre-decompose (legacy DEEP entrypoint
or LLM dropped the field).

Direct port of deprecated `graphs/youtube/adaptive.py:L196-205`."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


PLAN_FALLBACK_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


PLAN_FALLBACK_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research planner. Decompose the user's analytical "
        "question into 3-5 focused sub-questions that, when answered "
        "individually from video transcripts, will provide the evidence "
        "needed for a comprehensive analysis. Each sub-question should "
        "target a specific angle or pattern.",
    ),
    ("human", "{question}"),
])
