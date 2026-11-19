"""Prompt text for YCS transcript graph extraction and schema discovery."""
from __future__ import annotations

from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate

SYSTEM_PROMPT = (Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
SCHEMA_DISCOVERY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        (
            "You are a knowledge graph schema designer. Analyze the sample "
            "transcripts and suggest the most useful entity types and "
            "relationship types for building a knowledge graph. Focus on "
            "types that enable multi-hop reasoning and cross-document "
            "connections. Return 5-8 node types and 6-10 relationship types."
        ),
    ),
    (
        "human",
        "Sample transcripts:\n\n{samples}\n\nSuggest the best schema:",
    ),
])
