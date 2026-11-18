"""LLM-backed graph extraction component used by BD RAG indexing.

This fixed-call transformer is an explicit exception to the DeepAgent modules
in this package. It does not plan or invoke tools.
"""
from __future__ import annotations

from typing import Any

from langchain_neo4j import LLMGraphTransformer

import domains

from .... import prompts


def graph_transformer(model: Any) -> LLMGraphTransformer:
    """Build the configured graph transformer for BD retrieval indexing."""
    model = model.with_config(
        config = {
            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
    })
    return LLMGraphTransformer(
        llm = model,
        ignore_tool_usage = True,
        strict_mode = False,
        additional_instructions = prompts.rag.graph_transformer.prompts.SYSTEM_PROMPT,
    )
