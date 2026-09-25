"""OpenAlex search tool — wraps `api.openalex.org/works` as an MCP tool.
Free, keyless, no documented hard rate limit for polite (mailto-tagged)
callers, 320M+ works — surfaces open-access status/URL, topic names, and
cross-source external IDs (DOI · PubMed · PMC)."""
from __future__ import annotations
from . import config, domain, keys, schemas, service, tool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["config", "domain", "keys", "schemas", "service", "tool"]


def register(mcp: FastMCP) -> None:
    """Register the openalex_search tool on the root server."""
    tool.register(mcp)
