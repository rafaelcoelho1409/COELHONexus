"""Load the graph extractor's system instructions."""
import pathlib

SYSTEM_PROMPT = pathlib.Path(__file__).with_name("SYSTEM.md").read_text(encoding = "utf-8")
