"""BD planner always works inside one selected book and indexed Distill run."""
from pathlib import Path


SYSTEM_PROMPT = (Path(__file__).parent / "SYSTEM.md").read_text(encoding = "utf-8").strip()
