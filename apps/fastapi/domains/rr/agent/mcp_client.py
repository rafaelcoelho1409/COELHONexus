"""MCP client adapter for the RR agent.

Wraps `langchain-mcp-adapters.MultiServerMCPClient` as a process-wide
singleton pointed at the in-cluster FastMCP server. Returns LangChain
`BaseTool` objects that DeepAgents subagents can hold in their `tools=`
list.

Event-loop-aware cache (same pattern as the planner checkpointer in
domains/dd/planner/runtime/checkpoint/service.py): the client is bound
to the loop that opened it; Celery prefork workers run each task under a
fresh `asyncio.run(...)`, so a sticky cache would resolve against a
closed loop. We re-open whenever the running loop changes.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from .keys import MCP_SERVER_NAME, MCP_URL_DEFAULT, MCP_URL_ENV


logger = logging.getLogger(__name__)


_client: Optional[MultiServerMCPClient] = None
_tools_cache: Optional[list[BaseTool]] = None
_client_loop: Optional[asyncio.AbstractEventLoop] = None


def _mcp_url() -> str:
    """Resolve the FastMCP server URL — env override wins; falls back to
    the in-cluster ClusterIP."""
    raw = os.environ.get(MCP_URL_ENV, "").strip()
    return raw or MCP_URL_DEFAULT


async def get_mcp_tools() -> list[BaseTool]:
    """Singleton MCP client → list[BaseTool]. Re-opens on event-loop change
    so Celery prefork workers don't reuse a stale connection."""
    global _client, _tools_cache, _client_loop
    current_loop = asyncio.get_running_loop()
    if (
        _client is not None
        and _client_loop is current_loop
        and _tools_cache is not None
    ):
        return _tools_cache
    if _client is not None and _client_loop is not current_loop:
        logger.info(
            "[rr-mcp] event loop changed; dropping stale client and "
            "re-connecting on the current loop"
        )
        _client = None
        _tools_cache = None
        _client_loop = None
    url = _mcp_url()
    logger.info(f"[rr-mcp] connecting to {url}")
    _client = MultiServerMCPClient(
        {
            MCP_SERVER_NAME: {
                "url": url,
                "transport": "streamable_http",
            }
        }
    )
    _tools_cache = await _client.get_tools()
    _client_loop = current_loop
    logger.info(
        f"[rr-mcp] {len(_tools_cache)} tool(s) loaded: "
        f"{[t.name for t in _tools_cache]}"
    )
    return _tools_cache


async def get_tools_by_name(*names: str) -> list[BaseTool]:
    """Return BaseTool objects filtered by name. Each discovery subagent
    pulls only its ONE source tool to keep its tool list minimal (smaller
    tool list → cheaper + less prone to wrong-tool picks)."""
    all_tools = await get_mcp_tools()
    by_name = {t.name: t for t in all_tools}
    missing = [n for n in names if n not in by_name]
    if missing:
        raise RuntimeError(
            f"[rr-mcp] tool(s) not registered at {_mcp_url()}: {missing}. "
            f"Available: {list(by_name)}. Check apps/fastmcp/domains/rr/"
            f"tools/<source>/tool.py @mcp.tool(name=...) registration."
        )
    return [by_name[n] for n in names]


async def reset_mcp_client() -> None:
    """Drop the cached client + tools so the next call re-connects.
    Useful when the FastMCP server restarts mid-session."""
    global _client, _tools_cache, _client_loop
    _client = None
    _tools_cache = None
    _client_loop = None
    logger.info("[rr-mcp] client cache reset")
