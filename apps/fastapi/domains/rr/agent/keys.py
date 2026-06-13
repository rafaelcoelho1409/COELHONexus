"""Identifier registries for the RR agent.

Per docs/CODE-CONVENTIONS.md §2: identifier constants (tool names,
subagent names, env-var keys, service URLs, virtual-fs path conventions)
live in keys.py — NOT in params.py (numeric tunables) and NOT in
config.py (frozen-dataclass groups).
"""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# MCP tool names — must match @mcp.tool(name=...) in apps/fastmcp/domains/rr/
# tools/<source>/tool.py exactly. Wrong name = ToolNotFound at runtime.
# --------------------------------------------------------------------------- #
TOOL_ARXIV_SEARCH = "arxiv_search"
TOOL_S2_SEARCH    = "semantic_scholar_search"
TOOL_HF_DAILY     = "huggingface_daily_papers"
TOOL_HN_SEARCH    = "hn_search"


# --------------------------------------------------------------------------- #
# Subagent name registry — must match the "name" field of each subagent dict
# passed to create_deep_agent(subagents=[...]). The orchestrator references
# these names via the `task(name=...)` tool call.
# --------------------------------------------------------------------------- #
# 4 discovery subagents (one per source) — ACTIVE in subagents mode.
SUBAGENT_DISCOVERY_ARXIV = "discovery_arxiv"
SUBAGENT_DISCOVERY_S2    = "discovery_semantic_scholar"
SUBAGENT_DISCOVERY_HF    = "discovery_huggingface_daily_papers"
SUBAGENT_DISCOVERY_HN    = "discovery_hn"

# LLM-driven reasoning subagents — ACTIVE in both modes.
SUBAGENT_DEEP_READ  = "deep_read"
SUBAGENT_SYNTHESIS  = "synthesis"
SUBAGENT_REPORT     = "report"

# Active subagent registry (per mode, set at agent-build time)
SUBAGENT_NAMES_TOOLS_MODE: tuple[str, ...] = (
    SUBAGENT_DEEP_READ,
    SUBAGENT_SYNTHESIS,
)
SUBAGENT_NAMES_SUBAGENTS_MODE: tuple[str, ...] = (
    SUBAGENT_DISCOVERY_ARXIV,
    SUBAGENT_DISCOVERY_S2,
    SUBAGENT_DISCOVERY_HF,
    SUBAGENT_DISCOVERY_HN,
    SUBAGENT_DEEP_READ,
    SUBAGENT_SYNTHESIS,
    SUBAGENT_REPORT,
)

# --------------------------------------------------------------------------- #
# Discovery-mode env flag — picks which agent topology to wire at build time.
# Both modes ship in the codebase so the RR repo serves as a DeepAgents +
# FastMCP reference for both patterns.
# --------------------------------------------------------------------------- #
DISCOVERY_MODE_ENV    = "RR_DISCOVERY_MODE"
DISCOVERY_MODE_TOOLS  = "tools"
DISCOVERY_MODE_AGENTS = "subagents"
# Default = subagents (the learning / reference path). Override to "tools"
# (faster, deterministic) via the env var in Helm.
DISCOVERY_MODE_DEFAULT = DISCOVERY_MODE_AGENTS


# --------------------------------------------------------------------------- #
# Orchestrator-level tool names — deterministic phases that don't need an LLM.
# The orchestrator's LLM calls these by name.
#
# 2026-06-12 step-6 refactor: 4 discovery subagents replaced with 4 Python
# tools (discover_*). Eliminates JSON-truncation failure where the LLM had
# to copy 5KB MCP output into stash_discovery_result(papers_json=...).
# --------------------------------------------------------------------------- #
TOOL_DISCOVER_ARXIV = "discover_arxiv"
TOOL_DISCOVER_S2    = "discover_semantic_scholar"
TOOL_DISCOVER_HF    = "discover_huggingface_daily_papers"
TOOL_DISCOVER_HN    = "discover_hn"
TOOL_TRIAGE         = "triage_candidates"
TOOL_GRAPH_BUILD    = "graph_build_papers"


# --------------------------------------------------------------------------- #
# Virtual-FS path conventions — keys into the module-level dict in
# tools/state.py. Subagents access these via fs_tools.py @tools; tools
# in tools/triage.py + tools/graph_build.py call fs_read / fs_write
# directly.
# --------------------------------------------------------------------------- #
FS_DIR_DISCOVERY:   str = "discovery"
FS_DIR_TRIAGE:      str = "triage"
FS_DIR_EXTRACTIONS: str = "extractions"
FS_DIR_SYNTHESIS:   str = "synthesis"

# Fixed single-file paths
FS_FILE_TRIAGE_TOPN:      str = "triage/top_n.json"
FS_FILE_SYNTHESIS_REPORT: str = "synthesis/report.json"
FS_FILE_DIGEST:           str = "digest.json"


def fs_discovery_path(source: str) -> str:
    """Per-source discovery output path. `source` ∈ {'arxiv',
    'semantic_scholar', 'huggingface_daily_papers', 'hn'}."""
    return f"{FS_DIR_DISCOVERY}/{source}.json"


def fs_extraction_path(arxiv_id: str) -> str:
    """Per-paper deep_read extraction path."""
    return f"{FS_DIR_EXTRACTIONS}/{arxiv_id}.json"


# Source-name → fs_discovery_path lookup constants. Used by discovery
# subagent prompts to know the exact stash path.
FS_DISCOVERY_KEY_ARXIV: str = fs_discovery_path("arxiv")
FS_DISCOVERY_KEY_S2:    str = fs_discovery_path("semantic_scholar")
FS_DISCOVERY_KEY_HF:    str = fs_discovery_path("huggingface_daily_papers")
FS_DISCOVERY_KEY_HN:    str = fs_discovery_path("hn")


# --------------------------------------------------------------------------- #
# FastMCP server endpoint — in-cluster ClusterIP. Env-overridable so a REPL
# on the host can also test against a port-forwarded server.
# --------------------------------------------------------------------------- #
MCP_SERVER_NAME = "radar"
MCP_URL_ENV     = "FASTMCP_INTERNAL_URL"
MCP_URL_DEFAULT = "http://coelhonexus-fastmcp:8000/mcp/"
