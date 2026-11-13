"""Strict verifier policy for book-grounded BD answers."""
from pathlib import Path


SYSTEM_PROMPT = (Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
