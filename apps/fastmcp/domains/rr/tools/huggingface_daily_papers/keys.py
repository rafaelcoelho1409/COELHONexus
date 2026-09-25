"""Identifier registry for the HuggingFace Daily Papers tool — per docs/CODE-CONVENTIONS.md §2.

`keys.py` holds string identifiers consumed outside `service.py`.
Here: the MCP tool name shared by `tool.py` registration and the
rate-limiter declaration, so the two can't drift.
"""
from __future__ import annotations


TOOL_NAME: str = "huggingface_daily_papers"
