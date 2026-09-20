"""Pure functions for the RR agent's fs tools — no I/O, no event loop."""
from __future__ import annotations

import json
from typing import Any

from . import patterns


def parse_tool_message_content(content: Any) -> list[dict]:
    """Extract paper list[dict] from any ToolMessage.content shape
    langchain-mcp-adapters returns."""
    if content is None:
        return []
    if isinstance(content, list) and content and isinstance(content[0], dict) \
       and "type" not in content[0]:
        return [p for p in content if isinstance(p, dict)]
    if isinstance(content, list):
        texts: list[str] = []
        for block in content:
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
    if isinstance(content, str):
        try:
            data = json.loads(content)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    if isinstance(content, dict):
        if "text" in content:
            try:
                data = json.loads(content["text"])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                return []
        if "papers" in content and isinstance(content["papers"], list):
            return content["papers"]
    return []


# Smart quotes — common LLM emission artifacts that break JSON parsing.
_SMART_QUOTE_MAP = {
    '‘': "'", '’': "'",
    '“': '"', '”': '"',
    '–': '-', '—': '-',
}


def repair_json_escapes(text: str) -> str:
    r"""Multi-pass JSON repair for common LLM emission bugs.

    Passes (in order): smart-quote → ascii, truncated \u, stray backslashes,
    missing comma between }{, between ][, between "" on separate lines.
    """
    out = text
    for bad, good in _SMART_QUOTE_MAP.items():
        out = out.replace(bad, good)
    out = patterns.TRUNCATED_U_RE.sub(r'\\\\u', out)
    out = patterns.STRAY_BS_RE.sub(r'\\\\', out)
    out = patterns.MISSING_COMMA_BRACE_RE.sub(r'\1,\2\3', out)
    out = patterns.MISSING_COMMA_BRACKET_RE.sub(r'\1,\2\3', out)
    out = patterns.MISSING_COMMA_QUOTE_RE.sub(r'\1,\2\3', out)
    return out


def try_parse_with_repairs(text: str) -> tuple[Any, str | None]:
    """Strict-parse → repaired-parse → balanced-slice extraction. Raises JSONDecodeError if all fail."""
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        pass
    repaired = repair_json_escapes(text)
    try:
        return json.loads(repaired), "repair"
    except json.JSONDecodeError:
        pass
    first_brace = text.find('{')
    last_brace  = text.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        try:
            return (
                json.loads(repair_json_escapes(text[first_brace:last_brace + 1])),
                "slice_repair",
            )
        except json.JSONDecodeError:
            pass
    raise json.JSONDecodeError("all repair strategies failed", text, 0)
