"""Pure data normalization for BD RAG MCP results."""
from __future__ import annotations

from typing import Any


def to_plain_data(value: Any) -> Any:
    """Recursively convert MCP result values into JSON-compatible Python data."""
    if isinstance(value, dict):
        return {str(key): to_plain_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_plain_data(item) for item in value]
    if hasattr(value, "model_dump"):
        return to_plain_data(value.model_dump(mode = "json"))
    if hasattr(value, "__dict__"):
        return to_plain_data(vars(value))
    return value
