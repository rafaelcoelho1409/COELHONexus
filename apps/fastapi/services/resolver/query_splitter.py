"""
Split a multi-framework query into candidate name tokens.

  "LangChain + LangGraph + DeepAgents" → ["LangChain", "LangGraph", "DeepAgents"]
  "React, Vue, Svelte"                 → ["React", "Vue", "Svelte"]
  "Pydantic and FastAPI"               → ["Pydantic", "FastAPI"]
  "FastAPI"                            → ["FastAPI"]
"""

from __future__ import annotations

import re

_SPLIT_PATTERNS = [
    r"\s*\+\s*",
    r"\s*,\s*",
    r"\s*;\s*",
    r"\s+and\s+",
    r"\s+&\s+",
]

_MAX_CANDIDATES = 12  # bounds parallelism + abuse


def split_query(raw: str) -> list[str]:
    if not raw or not raw.strip():
        return []
    pieces: list[str] = [raw.strip()]
    for pattern in _SPLIT_PATTERNS:
        nxt: list[str] = []
        for piece in pieces:
            nxt.extend(re.split(pattern, piece))
        pieces = nxt
    seen: set[str] = set()
    out: list[str] = []
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= _MAX_CANDIDATES:
            break
    return out
