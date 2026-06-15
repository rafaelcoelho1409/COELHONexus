"""ycs/rag/adaptive/nodes/synthesize — DEEP-path merger prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L113-132`,
extended 2026-06-14 with a `MessagesPlaceholder("history")` slot so the
synthesizer can continue a multi-turn DEEP-mode investigation — e.g.
the user follows "What patterns emerge?" with "Now compare them by
cost" and the synthesizer references its prior framing instead of
restarting from zero. See `standard/nodes/generate/prompts.py` for
the broader rationale."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


SYNTHESIZE_PROMPT_VERSION = "history-2026-06-14"


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
        "sub-research found.\n\n"
        "Prior conversation turns (if any) are provided below as "
        "context for follow-up DEEP queries — reference them to stay "
        "coherent across turns, but ground every new factual claim in "
        "the sub-research findings attached to THIS turn.",
    ),
    MessagesPlaceholder("history"),
    (
        "human",
        "Original question: {question}\n\n"
        "Research plan: {research_plan}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Synthesize these findings into a comprehensive analytical report.",
    ),
])
