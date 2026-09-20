"""Identifier registries for the RR agent — tool names, subagent names, fs paths, env vars."""
from __future__ import annotations


# MCP tool names — must match @mcp.tool(name=...) in apps/fastmcp/domains/rr/tools/<source>/tool.py.
TOOL_ARXIV_SEARCH    = "arxiv_search"
TOOL_S2_SEARCH       = "semantic_scholar_search"
TOOL_HF_DAILY        = "huggingface_daily_papers"
TOOL_HN_SEARCH       = "hn_search"
TOOL_OPENALEX_SEARCH = "openalex_search"


# Subagent names — must match the "name" field in each subagent dict passed to create_deep_agent.
SUBAGENT_DISCOVERY_ARXIV    = "discovery_arxiv"
SUBAGENT_DISCOVERY_S2       = "discovery_semantic_scholar"
SUBAGENT_DISCOVERY_HF       = "discovery_huggingface_daily_papers"
SUBAGENT_DISCOVERY_HN       = "discovery_hn"
SUBAGENT_DISCOVERY_OPENALEX = "discovery_openalex"

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
    SUBAGENT_DISCOVERY_OPENALEX,
    SUBAGENT_DEEP_READ,
    SUBAGENT_SYNTHESIS,
)

# Discovery-mode env flag — both modes shipped so the repo covers both DeepAgents patterns.
DISCOVERY_MODE_ENV    = "RR_DISCOVERY_MODE"
DISCOVERY_MODE_TOOLS  = "tools"
DISCOVERY_MODE_AGENTS = "subagents"
DISCOVERY_MODE_DEFAULT = DISCOVERY_MODE_AGENTS


# Orchestrator-level tool names for deterministic phases.
TOOL_DISCOVER_ARXIV    = "discover_arxiv"
TOOL_DISCOVER_S2       = "discover_semantic_scholar"
TOOL_DISCOVER_HF       = "discover_huggingface_daily_papers"
TOOL_DISCOVER_HN       = "discover_hn"
TOOL_DISCOVER_OPENALEX = "discover_openalex"
TOOL_TRIAGE            = "triage_candidates"
TOOL_GRAPH_BUILD       = "graph_build_papers"


# Virtual-FS path conventions — keys into the module-level dict in tools/state.py.
FS_DIR_DISCOVERY:   str = "discovery"
FS_DIR_TRIAGE:      str = "triage"
FS_DIR_EXTRACTIONS: str = "extractions"
FS_DIR_SYNTHESIS:   str = "synthesis"

FS_FILE_TRIAGE_TOPN:      str = "triage/top_n.json"
FS_FILE_SYNTHESIS_REPORT: str = "synthesis/report.json"
FS_FILE_DIGEST:           str = "digest.json"

# Written by graph_build_papers on completion (any outcome, even 0
# persisted) so a redispatch can be hard-blocked the same way a
# finished discovery source or synthesis report already is. graph_build
# has no other single fs marker of its own — it only writes to
# Neo4j/Qdrant, not fs.
FS_FILE_GRAPH_BUILD_DONE: str = "graph_build/done.json"


def fs_discovery_path(source: str) -> str:
    """Per-source discovery output path. `source` ∈ {'arxiv', 'semantic_scholar', 'huggingface_daily_papers', 'hn', 'openalex'}."""
    return f"{FS_DIR_DISCOVERY}/{source}.json"


def fs_extraction_path(arxiv_id: str) -> str:
    return f"{FS_DIR_EXTRACTIONS}/{arxiv_id}.json"


FS_DISCOVERY_KEY_ARXIV:    str = fs_discovery_path("arxiv")
FS_DISCOVERY_KEY_S2:       str = fs_discovery_path("semantic_scholar")
FS_DISCOVERY_KEY_HF:       str = fs_discovery_path("huggingface_daily_papers")
FS_DISCOVERY_KEY_HN:       str = fs_discovery_path("hn")
FS_DISCOVERY_KEY_OPENALEX: str = fs_discovery_path("openalex")

# All 5 discovery files must be present before discovery is considered
# complete. An empty-list stash is the correct empty-result signal;
# absence means the subagent didn't run. Used by PhaseEnforcerMiddleware.
REQUIRED_DISCOVERY_KEYS: tuple[str, ...] = (
    FS_DISCOVERY_KEY_ARXIV,
    FS_DISCOVERY_KEY_S2,
    FS_DISCOVERY_KEY_HF,
    FS_DISCOVERY_KEY_HN,
    FS_DISCOVERY_KEY_OPENALEX,
)

# Discovery source name (as embedded in fs paths) → subagent name. Used by
# PhaseEnforcerMiddleware to build task() dispatch nudges for missing sources.
DISCOVERY_SOURCE_TO_SUBAGENT: dict[str, str] = {
    "arxiv":                    SUBAGENT_DISCOVERY_ARXIV,
    "semantic_scholar":         SUBAGENT_DISCOVERY_S2,
    "huggingface_daily_papers": SUBAGENT_DISCOVERY_HF,
    "hn":                       SUBAGENT_DISCOVERY_HN,
    "openalex":                 SUBAGENT_DISCOVERY_OPENALEX,
}

# Reverse of the above — subagent name → its fs discovery key. Used by
# PhaseEnforcerMiddleware.wrap_tool_call to hard-block a task() redispatch
# of a discovery subagent whose source already has a resolved (non-None)
# fs entry, instead of relying on the subagent's own stash-tool guard
# (which still lets a full wasted turn run before it self-corrects).
SUBAGENT_TO_DISCOVERY_KEY: dict[str, str] = {
    SUBAGENT_DISCOVERY_ARXIV:    FS_DISCOVERY_KEY_ARXIV,
    SUBAGENT_DISCOVERY_S2:       FS_DISCOVERY_KEY_S2,
    SUBAGENT_DISCOVERY_HF:       FS_DISCOVERY_KEY_HF,
    SUBAGENT_DISCOVERY_HN:       FS_DISCOVERY_KEY_HN,
    SUBAGENT_DISCOVERY_OPENALEX: FS_DISCOVERY_KEY_OPENALEX,
}


# FastMCP server endpoint — in-cluster Service DNS; env-overridable for local port-forward
# testing. Port is 8001, not the container's actual listening port (8000) — see
# k8s/helm/values.yaml's fastmcp.portsSettings comment (Service port deliberately
# differs from targetPort to avoid a k3d svclb hostPort collision with fastapi).
MCP_SERVER_NAME = "radar"
MCP_URL_ENV     = "FASTMCP_INTERNAL_URL"
MCP_URL_DEFAULT = "http://coelhonexus-fastmcp:8001/mcp/"
