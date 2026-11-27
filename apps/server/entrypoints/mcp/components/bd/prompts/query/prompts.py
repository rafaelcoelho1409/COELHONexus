"""Load BD Query prompt templates from adjacent Markdown files."""
from __future__ import annotations

from pathlib import Path


PROMPT_DIRECTORY = Path(__file__).parent
DRAFT_QUERY_TEMPLATE = (PROMPT_DIRECTORY / "DRAFT_QUERY.md").read_text(
    encoding = "utf-8",
).strip()
INVESTIGATE_TEMPLATE = (PROMPT_DIRECTORY / "INVESTIGATE.md").read_text(
    encoding = "utf-8",
).strip()
