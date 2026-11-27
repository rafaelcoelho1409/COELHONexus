"""Load the adaptive YCS Ask prompt template."""
from __future__ import annotations

from pathlib import Path


PROMPT_TEMPLATE = (Path(__file__).parent / "PROMPT.md").read_text(
    encoding="utf-8",
).strip()
