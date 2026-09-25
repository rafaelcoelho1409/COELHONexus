"""Semantic Scholar search tool — wraps `api.semanticscholar.org/graph/v1/paper/search`
as an MCP tool, with S2-unique signals (TLDR · influentialCitationCount ·
externalIds for cross-source dedup with arxiv · openAccessPdf)."""
from __future__ import annotations
from . import config, domain, keys, schemas, service, tool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["config", "domain", "keys", "schemas", "service", "tool"]


def register(mcp: FastMCP) -> None:
    """Register the semantic_scholar_search tool on the root server."""
    tool.register(mcp)
