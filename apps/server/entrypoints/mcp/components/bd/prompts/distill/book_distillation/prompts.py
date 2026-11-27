"""BD Distill workflow prompt template."""
from __future__ import annotations

from pathlib import Path


PROMPT_TEMPLATE = (Path(__file__).parent / "PROMPT.md").read_text(encoding = "utf-8").strip()
RETRIEVAL_PROMPT_TEMPLATE = (Path(__file__).parent / "RETRIEVAL_PROMPT.md").read_text(
    encoding = "utf-8",
).strip()
