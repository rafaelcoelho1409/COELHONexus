"""YCS transcript graph extraction through LangChain's graph transformer."""
from __future__ import annotations
import domains
from .... import prompts

from typing import Any

from langchain_neo4j import LLMGraphTransformer


def graph_transformer(
    model: Any,
    *,
    run_id: str | None = None,
    additional_instructions: str = "",
    unstructured: bool = False,
) -> LLMGraphTransformer:
    """Create the YCS transcript transformer with usage attribution and safe fallbacks."""
    if run_id:
        model = model.with_config(
            config = {
                "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
            },
        )
    instructions = prompts.rag.graph_transformer.prompts.SYSTEM_PROMPT
    if additional_instructions:
        instructions = f"{instructions}\n\n{additional_instructions}"
    options: dict[str, Any] = {
        "llm": model,
        "strict_mode": False,
        "additional_instructions": instructions,
    }
    if unstructured:
        options["ignore_tool_usage"] = True
    else:
        options["node_properties"] = ["evidence"]
        options["relationship_properties"] = ["evidence"]
    return LLMGraphTransformer(**options)
