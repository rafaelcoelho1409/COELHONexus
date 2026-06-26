"""Identifier registries for the RR agent — tool names, subagent names, fs paths, env vars."""
from __future__ import annotations


# MCP tool names — must match @mcp.tool(name=...) in apps/fastmcp/domains/rr/tools/<source>/tool.py.
TOOL_ARXIV_SEARCH = "arxiv_search"
TOOL_S2_SEARCH    = "semantic_scholar_search"
TOOL_HF_DAILY     = "huggingface_daily_papers"
TOOL_HN_SEARCH    = "hn_search"


# Subagent names — must match the "name" field in each subagent dict passed to create_deep_agent.
SUBAGENT_DISCOVERY_ARXIV = "discovery_arxiv"
SUBAGENT_DISCOVERY_S2    = "discovery_semantic_scholar"
SUBAGENT_DISCOVERY_HF    = "discovery_huggingface_daily_papers"
SUBAGENT_DISCOVERY_HN    = "discovery_hn"

SUBAGENT_DEEP_READ  = "deep_read"
SUBAGENT_SYNTHESIS  = "synthesis"
SUBAGENT_REPORT     = "report"

# SUBAGENT_REPORT removed from subagents mode — synthesis owns per-paper themes;
# digest assembly is Python. Constant retained for reference.
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
)

# Discovery-mode env flag — both modes shipped so the repo covers both DeepAgents patterns.
DISCOVERY_MODE_ENV    = "RR_DISCOVERY_MODE"
DISCOVERY_MODE_TOOLS  = "tools"
DISCOVERY_MODE_AGENTS = "subagents"
DISCOVERY_MODE_DEFAULT = DISCOVERY_MODE_AGENTS


# Orchestrator-level tool names for deterministic phases.
TOOL_DISCOVER_ARXIV = "discover_arxiv"
TOOL_DISCOVER_S2    = "discover_semantic_scholar"
TOOL_DISCOVER_HF    = "discover_huggingface_daily_papers"
TOOL_DISCOVER_HN    = "discover_hn"
TOOL_TRIAGE         = "triage_candidates"
TOOL_GRAPH_BUILD    = "graph_build_papers"


# Virtual-FS path conventions — keys into the module-level dict in tools/state.py.
FS_DIR_DISCOVERY:   str = "discovery"
FS_DIR_TRIAGE:      str = "triage"
FS_DIR_EXTRACTIONS: str = "extractions"
FS_DIR_SYNTHESIS:   str = "synthesis"

FS_FILE_TRIAGE_TOPN:      str = "triage/top_n.json"
FS_FILE_SYNTHESIS_REPORT: str = "synthesis/report.json"
FS_FILE_DIGEST:           str = "digest.json"


def fs_discovery_path(source: str) -> str:
    """Per-source discovery output path. `source` ∈ {'arxiv', 'semantic_scholar', 'huggingface_daily_papers', 'hn'}."""
    return f"{FS_DIR_DISCOVERY}/{source}.json"


def fs_extraction_path(arxiv_id: str) -> str:
    return f"{FS_DIR_EXTRACTIONS}/{arxiv_id}.json"


FS_DISCOVERY_KEY_ARXIV: str = fs_discovery_path("arxiv")
FS_DISCOVERY_KEY_S2:    str = fs_discovery_path("semantic_scholar")
FS_DISCOVERY_KEY_HF:    str = fs_discovery_path("huggingface_daily_papers")
FS_DISCOVERY_KEY_HN:    str = fs_discovery_path("hn")


# FastMCP server endpoint — in-cluster ClusterIP; env-overridable for local port-forward testing.
MCP_SERVER_NAME = "radar"
MCP_URL_ENV     = "FASTMCP_INTERNAL_URL"
MCP_URL_DEFAULT = "http://coelhonexus-fastmcp:8000/mcp/"
