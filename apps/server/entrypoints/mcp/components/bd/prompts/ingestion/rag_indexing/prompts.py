"""Load the BD RAG indexing MCP prompt template from its adjacent file."""
from __future__ import annotations

from pathlib import Path

PROMPT_TEMPLATE = (Path(__file__).parent / "PROMPT.md").read_text(encoding = "utf-8").strip()
