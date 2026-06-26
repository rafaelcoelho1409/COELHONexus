"""ycs/rag/adaptive/nodes/critic — DEEP-path validation prompt.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


CRITIC_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research quality critic. Evaluate the synthesis "
        "against the sub-research findings. Check:\n"
        "1. Is every claim in the synthesis supported by at least one "
        "sub-research finding?\n"
        "2. Are there contradictions within the synthesis itself?\n"
        "3. Did the synthesis adequately cover all sub-questions?\n"
        "4. Assign a confidence score from 0.0 (unreliable) to 1.0 "
        "(fully supported).\n"
        "Be strict but fair.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Synthesis:\n{synthesis}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Evaluate the synthesis.",
    ),
])
