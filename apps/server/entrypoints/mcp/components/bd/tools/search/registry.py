"""Register BD Search tools on a FastMCP server."""
from __future__ import annotations
from . import params, tools

from fastmcp import FastMCP
from mcp.types import ToolAnnotations


_READ_ONLY = ToolAnnotations(readOnlyHint = True, destructiveHint = False, idempotentHint = True)
_OPEN_WORLD = ToolAnnotations(
    readOnlyHint = True,
    destructiveHint = False,
    idempotentHint = True,
    openWorldHint = True,
)


def register_tools(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    """Register the BD retrieval tools."""
    handlers = tools.BDSearchTools(trusted_in_process = trusted_in_process)
    mcp.tool(
        name = "bd_search_elasticsearch",
        description = "Search the current published BD book index with bounded BM25 retrieval.",
        annotations = _READ_ONLY,
        timeout = params.RETRIEVAL_TIMEOUT_S,
    )(handlers.search_elasticsearch)
    mcp.tool(
        name = "bd_search_qdrant",
        description = "Search the current published BD book index with dense and sparse hybrid retrieval.",
        annotations = _READ_ONLY,
        timeout = params.RETRIEVAL_TIMEOUT_S,
    )(handlers.search_qdrant)
    mcp.tool(
        name = "bd_search_neo4j",
        description = "Retrieve entity and relationship evidence from the current published BD book graph.",
        annotations = _READ_ONLY,
        timeout = params.RETRIEVAL_TIMEOUT_S,
    )(handlers.search_neo4j)
    mcp.tool(
        name = "bd_search_web",
        description = "Search the selected Settings Search MCP provider for bounded external context.",
        annotations = _OPEN_WORLD,
        timeout = params.WEB_SEARCH_TIMEOUT_S,
    )(handlers.search_web)
