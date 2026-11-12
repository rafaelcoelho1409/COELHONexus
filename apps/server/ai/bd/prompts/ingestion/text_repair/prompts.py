"""Prompt text for the text-repair role."""
import pathlib


SYSTEM_PROMPT = (pathlib.Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
