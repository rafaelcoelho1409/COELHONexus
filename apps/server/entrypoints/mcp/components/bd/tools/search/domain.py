"""Pure query validation for BD Search MCP."""

from __future__ import annotations

def normalize_query(value: str, max_chars: int) -> str:
    return " ".join(value.split())[:max_chars].strip()
