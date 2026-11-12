"""Prompt text for book metadata normalization."""
import pathlib


SYSTEM_PROMPT = (pathlib.Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
