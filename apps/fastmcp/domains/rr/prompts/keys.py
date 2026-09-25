"""Prompt-name registry — per docs/CODE-CONVENTIONS.md §2.

Routing names consumed outside the defining module (by the `@mcp.prompt`
boundary, docs, and smoke scripts) live here so registration and
references can't drift. Same shape as each tool's `keys.TOOL_NAME`.
"""
from __future__ import annotations


DIGEST_TODAY: str = "digest_today"
