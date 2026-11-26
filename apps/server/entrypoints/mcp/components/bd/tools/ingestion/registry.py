"""Register typed BD Agentic Ingestion lifecycle tools."""
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
    handlers = tools.BDIngestionTools(trusted_in_process = trusted_in_process)
    mcp.tool(
        name = "bd_ingestion_get_run",
        description = "Read a compact BD Agentic Ingestion run status and progress snapshot.",
        annotations = _READ_ONLY,
        timeout = params.READ_TIMEOUT_S,
    )(handlers.get_run)
    mcp.tool(
        name = "bd_ingestion_get_quality_summary",
        description = "Read aggregate quality counts for one BD Agentic Ingestion run.",
        annotations = _READ_ONLY,
        timeout = params.READ_TIMEOUT_S,
    )(handlers.get_quality_summary)
    mcp.tool(
        name = "bd_ingestion_start_run",
        description = "Queue BD Agentic Ingestion against a completed extraction and return its run ID.",
        annotations = _IDEMPOTENT_COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.start_run)
    mcp.tool(
        name = "bd_ingestion_resume_run",
        description = "Resume a failed or cancelled BD Agentic Ingestion run from its saved checkpoint.",
        annotations = _COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.resume_run)
    mcp.tool(
        name = "bd_ingestion_cancel_run",
        description = "Request cooperative cancellation of a BD Agentic Ingestion run.",
        annotations = _COMMAND,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.cancel_run)
    mcp.tool(
        name = "bd_ingestion_replay_from_node",
        description = "Replay one allowlisted graph node and descendants after explicit confirmation.",
        annotations = _REPLAY,
        timeout = params.TOOL_TIMEOUT_S,
    )(handlers.replay_from_node)
