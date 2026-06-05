"""off_topic — pure helpers (verdict parser).

Prompt strings + the head+tail input prep + anchor descriptors live in
prompts.py.
"""
from __future__ import annotations


def parse_verdict(text: str) -> bool | None:
    """Parse the LLM's one-word verdict. Returns True for KEEP, False for
    DROP, None if unparseable (caller decides fallback)."""
    if not text:
        return None
    head = text.strip().upper().split()[0].strip(".,;:!\"'`)")
    if head == "KEEP":
        return True
    if head == "DROP":
        return False
    return None
