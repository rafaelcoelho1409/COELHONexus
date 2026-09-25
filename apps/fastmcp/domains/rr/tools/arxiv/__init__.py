"""arXiv search tool — wraps export.arxiv.org/api/query as an MCP tool."""
from __future__ import annotations
from . import config, domain, keys, schemas, service, tool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["config", "domain", "keys", "schemas", "service", "tool"]


def register(mcp: FastMCP) -> None:
    """Register the arxiv_search tool on the root server."""
    tool.register(mcp)
