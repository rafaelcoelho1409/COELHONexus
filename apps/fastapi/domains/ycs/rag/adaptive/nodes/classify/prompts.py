"""ycs/rag/adaptive/nodes/classify — query-complexity + scope-detect prompt.

Direct port of deprecated `schemas/youtube/prompts.py:L74-101`. Single
LLM call returns BOTH the mode (`fast` / `standard` / `deep`) and any
channel/person names mentioned in the query — the latter feeds the
auto-scope lookup against Neo4j."""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


CLASSIFY_PROMPT_VERSION = "deprecated-1:1-2026-06-06"


CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query complexity classifier for a YouTube transcript "
        "search system. Classify the user's question into one of three "
        "modes:\n\n"
        "FAST — Simple factual questions answerable from general knowledge. "
        "Examples: 'What is citizenship by investment?', "
        "'What does CBI stand for?'\n\n"
        "STANDARD — Questions that need evidence from video transcripts. "
        "Examples: 'What does Wealthy Expat say about Dubai?', "
        "'Compare Dominica vs Grenada for citizenship', "
        "'What are the tax benefits of living in Dubai?'\n\n"
        "DEEP — Analytical questions requiring multi-faceted analysis "
        "across many videos. Pattern-finding, psychological analysis, "
        "contradiction detection, hidden assumptions. Examples: "
        "'What psychological traits does this creator show?', "
        "'What contradictions exist across all videos?', "
        "'What hidden assumptions does this channel never question?'\n\n"
        "When uncertain, default to STANDARD.\n"
        "For DEEP mode, also generate 3-8 focused sub-questions that "
        "break down the analysis.\n\n"
        "SCOPE DETECTION: Identify any specific channel or person names "
        "mentioned in the query. Return them in channel_names so "
        "retrieval can be scoped to their content only.\n"
        "Examples:\n"
        "- 'What does Vitoria Stecca think about X?' → channel_names: "
        "['Vitoria Stecca']\n"
        "- 'Compare Rafael Cintron and Vitoria Stecca' → channel_names: "
        "['Rafael Cintron', 'Vitoria Stecca']\n"
        "- 'What are the best tax strategies?' → channel_names: [] "
        "(no specific person/channel)\n"
        "If the query is about a SPECIFIC person/channel, always include "
        "their name.",
    ),
    ("human", "{question}"),
])
