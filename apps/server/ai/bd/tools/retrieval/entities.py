"""Per-source evidence captured during one specialist-agent invocation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal


RetrievalSource = Literal["elasticsearch", "neo4j", "qdrant"]


@dataclass(slots = True)
class RetrievalSession:
    book_id: str
    run_id: str
    source: RetrievalSource
    ranked_lists: list[list[dict[str, Any]]] = field(default_factory = list)
    queries: list[str] = field(default_factory = list)
    statuses: list[str] = field(default_factory = list)
    error_type: str | None = None
    tool_calls: int = 0
    lock: asyncio.Lock = field(default_factory = asyncio.Lock)
