"""Run-scoped state retained by the shared BD Search MCP tool."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal


SearchStatus = Literal[
    "disabled",
    "invalid_query",
    "no_results",
    "complete",
    "query_limit",
    "unavailable",
    "unsupported_provider",
]


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
    reader_initialized: bool = False
    mcp_client: Any = None
    remote_read_tool: Any = None
    remote_tools: list[Any] = field(default_factory = list)
    source_texts: dict[str, str] = field(default_factory = dict)
    read_source_ids: set[str] = field(default_factory = set)
    lock: asyncio.Lock = field(default_factory = asyncio.Lock)
