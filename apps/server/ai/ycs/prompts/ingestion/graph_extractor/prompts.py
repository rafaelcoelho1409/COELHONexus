"""Load the graph extractor's system instructions."""
from pathlib import Path

SYSTEM_PROMPT = Path(__file__).with_name("SYSTEM.md").read_text(encoding = "utf-8")
