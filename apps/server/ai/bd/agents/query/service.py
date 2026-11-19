"""Invocation helpers for the BD Query DeepAgent."""
from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

from . import agent


async def generate_query(
    *,
    backend: str,
    user_prompt: str,
    previous: str,
    schema: dict[str, Any],
    repair_error: str | None = None,
) -> AIMessage:
    """Invoke the BD Query DeepAgent and return its final assistant message."""
    details = {
        "backend": backend,
        "user_request": user_prompt,
        "previous_query": previous,
        "schema": schema,
        "validation_error": repair_error,
    }
    result = await agent.query_agent().ainvoke({
        "messages": [{
            "role": "user",
            "content": json.dumps(details, ensure_ascii = False, default = str),
        }],
        "files": {},
    })
    messages = result.get("messages") if isinstance(result, dict) else None
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, AIMessage):
                return message
    raise ValueError("the BD Query agent returned no assistant message")
