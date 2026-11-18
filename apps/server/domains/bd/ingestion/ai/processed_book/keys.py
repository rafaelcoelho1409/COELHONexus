"""Stable identifiers for entries in the canonical processed-book contents."""
from __future__ import annotations

CONTENTS_ENTRY_ID_PREFIX = "contents-"
CONTENTS_ENTRY_ID_START = 1
CONTENTS_ENTRY_ID_WIDTH = 5


def contents_entry_id(index: int) -> str:
    """Format the one-based canonical contents-entry identifier."""
    return f"{CONTENTS_ENTRY_ID_PREFIX}{index:0{CONTENTS_ENTRY_ID_WIDTH}d}"
