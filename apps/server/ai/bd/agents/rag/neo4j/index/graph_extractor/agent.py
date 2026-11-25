"""BD Docling Graph agent for processed-book knowledge graph extraction."""
from __future__ import annotations
import domains
from . import service
from ...... import prompts

from typing import Any


def graph_extractor(model: Any) -> service.GraphExtractionAgent:
    """Build the BD graph extraction agent with usage attribution and prompt."""
    configured_model = model.with_config(
        config={
            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
        },
    )
    return service.GraphExtractionAgent(
        model=configured_model,
        instructions=prompts.rag.graph_extractor.prompts.SYSTEM_PROMPT,
    )
