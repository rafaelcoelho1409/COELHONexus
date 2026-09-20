"""Pure functions for the RR agent's discovery tools — no I/O."""
from __future__ import annotations

import json
from typing import Any


def parse_mcp_result(result: Any) -> list[dict]:
    """Best-effort extraction of paper list[dict] from any MCP return shape.

    langchain-mcp-adapters returns the tool result in several shapes
    depending on adapter version + tool. Handle all:
      - list[dict]            (already-parsed paper records)
      - str                   (JSON-encoded list)
      - list[TextContent]     ([{"type":"text", "text":"<JSON>"}, ...])
      - dict with "text" key  ({"type":"text", "text":"<JSON>"})
    """
    if result is None:
        return []
    # Direct list of paper dicts
    if isinstance(result, list) and result and isinstance(result[0], dict) \
       and "type" not in result[0]:
        return [p for p in result if isinstance(p, dict)]
    # List of TextContent blocks
    if isinstance(result, list):
        texts: list[str] = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    texts.append(t)
        combined = "".join(texts)
        if combined:
            try:
                data = json.loads(combined)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        return []
    # Single JSON string
    if isinstance(result, str):
        try:
            data = json.loads(result)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    # Wrapped TextContent dict
    if isinstance(result, dict):
        if "text" in result:
            try:
                data = json.loads(result["text"])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                return []
        if "papers" in result and isinstance(result["papers"], list):
            return result["papers"]
    return []
