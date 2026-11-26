"""Remote MCP transport and client construction for Settings integrations."""
from __future__ import annotations
from . import entities, params

import asyncio
from typing import TYPE_CHECKING

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from mcp.types import Tool

if TYPE_CHECKING:
    from langchain.mcp import MCPAdapter


def build_client(
    connection: entities.MCPConnection,
    *,
    timeout_s: float | None = None,
) -> Client:
    """Build a FastMCP client using a configured connection's transport data."""
    transport = StreamableHttpTransport(
        connection.endpoint,
        headers = dict(connection.headers) if connection.headers else None,
    )
    return Client(
        transport,
        name = connection.provider_id,
        timeout = timeout_s if timeout_s is not None else params.CLIENT_TIMEOUT_S,
    )


async def list_tools(
    connection: entities.MCPConnection,
    *,
    timeout_s: float | None = None,
) -> list[Tool]:
    """Connect to a remote MCP server and return its advertised tools."""
    client = build_client(connection, timeout_s = timeout_s)
    async with client:
        return await asyncio.wait_for(
            client.list_tools(),
            timeout = timeout_s if timeout_s is not None else params.CLIENT_TIMEOUT_S,
        )


def build_langchain_mcp_adapter(
    connection: entities.MCPConnection,
    *,
    timeout_s: float | None = None,
) -> MCPAdapter:
    """Build LangChain's tool adapter over a configured remote MCP client."""
    from langchain.mcp import MCPAdapter

    return MCPAdapter(build_client(connection, timeout_s = timeout_s))
