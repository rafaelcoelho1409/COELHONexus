"""Run-scoped state retained by the shared BD Search MCP tool."""
from __future__ import annotations

import asyncio

from dataclasses import dataclass, field
from typing import Any, Literal


SearchStatus = Literal["unavailable", "unsupported_provider", "no_results", "complete"]


@dataclass(slots = True)
class SearchSession:
    """Bound search results and source citations to one agent run."""

    run_id: str
    status: SearchStatus = "no_results"
    provider_id: str | None = None
    queries: list[str] = field(default_factory = list)
    sources: dict[str, dict[str, Any]] = field(default_factory = dict)
    query_results: dict[str, list[dict[str, Any]]] = field(default_factory = dict)
    error_type: str | None = None
    initialized: bool = False
    mcp_client: Any = None
    remote_tool: Any = None
    lock: asyncio.Lock = field(default_factory = asyncio.Lock)
