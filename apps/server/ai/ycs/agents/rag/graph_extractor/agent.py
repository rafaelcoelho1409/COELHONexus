"""Configure the YCS chat model for Docling Graph transcript extraction."""
from __future__ import annotations

from typing import Any

import domains


def graph_model(model: Any, *, run_id: str | None = None) -> Any:
    """Attach per-run token attribution to the configured chat model."""
    if not run_id:
        return model
    return model.with_config(
        config={
            "callbacks": [domains.settings.llm_counter.service.LLMCounterCallback()],
        },
    )
