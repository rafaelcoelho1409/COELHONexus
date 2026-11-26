"""Register typed BD Agentic RAG index lifecycle tools."""
from __future__ import annotations

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import params, tools

_READ_ONLY = ToolAnnotations(read_only_hint = True, destructive_hint = False, idempotent_hint = True)
_IDEMPOTENT_COMMAND = ToolAnnotations(
    read_only_hint = False,
    destructive_hint = False,
    idempotent_hint = True,
)
_COMMAND = ToolAnnotations(read_only_hint = False, destructive_hint = False, idempotent_hint = False)
_REPLAY = ToolAnnotations(read_only_hint = False, destructive_hint = True, idempotent_hint = False)


def register_tools(mcp: FastMCP, *, trusted_in_process: bool = False) -> None:
    handlers = tools.BDRetrievalIndexTools(trusted_in_process = trusted_in_process)
    mcp.tool(
        name = "bd_rag_index_get_run",
        description = "Read durable progress and store counts for one BD Agentic RAG index run.",
        annotations = _READ_ONLY,
        timeout = params.READ_TIMEOUT_S,
    )(handlers.get_run)
    mcp.tool(
        name = "bd_rag_index_get_current",
        description = "Read the published BD index summary currently used by retrieval.",
        annotations = _READ_ONLY,
        timeout = params.READ_TIMEOUT_S,
    )(handlers.get_current_index)
    mcp.tool(
        name = "bd_rag_index_start",
        description = "Queue indexing for a completed BD Agentic Ingestion run.",
        annotations = _IDEMPOTENT_COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.start_index)
    mcp.tool(
        name = "bd_rag_index_resume",
        description = "Resume a failed, cancelled, or stale BD Agentic RAG index run.",
        annotations = _COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.resume_index)
    mcp.tool(
        name = "bd_rag_index_cancel",
        description = "Request cooperative cancellation of a BD Agentic RAG index run.",
        annotations = _IDEMPOTENT_COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.cancel_index)
    mcp.tool(
        name = "bd_rag_index_replay_from_node",
        description = "Replay one allowlisted index node and downstream stores after confirmation.",
        annotations = _REPLAY,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.replay_from_node)
