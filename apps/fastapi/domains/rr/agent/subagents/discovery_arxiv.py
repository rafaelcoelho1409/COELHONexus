"""arXiv discovery subagent (active in SUBAGENTS mode).

The DeepAgents subagent pattern at its purest:
  - holds ONE MCP tool (arxiv_search) + ONE fs tool (stash_discovery_result)
  - its system_prompt is augmented with two skills: arxiv_query_shaping
    (how to construct args) + rotator_etiquette (how to behave inside
    the rotator cascade)
  - the orchestrator dispatches it via `task(subagent_type=...)`
  - DeepAgents gives this subagent an ISOLATED context so the orchestrator
    never sees the bulky MCP tool result

In TOOLS mode, this file is dormant — replaced by the deterministic
discover_arxiv Python @tool in `agent/tools/discovery.py`. Both code
paths ship in the repo to serve as a DeepAgents reference.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_DISCOVERY_ARXIV, TOOL_ARXIV_SEARCH
from ..mcp_client import get_tools_by_name
from ..prompts import DISCOVERY_ARXIV_SYSTEM_PROMPT
from ..skills import SKILL_ARXIV_QUERY_SHAPING, SKILL_ROTATOR_ETIQUETTE
from ..tools.fs_tools import stash_discovery_result


async def build_discovery_arxiv(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the arXiv discovery worker (SUBAGENTS mode)."""
    mcp_tools = await get_tools_by_name(TOOL_ARXIV_SEARCH)
    full_prompt = (
        f"=== SKILL: arxiv_query_shaping ===\n\n"
        f"{SKILL_ARXIV_QUERY_SHAPING}\n\n"
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{DISCOVERY_ARXIV_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_DISCOVERY_ARXIV,
        "description": (
            "Searches arXiv for preprints matching the user's interest "
            "verticals. Calls arxiv_search MCP tool, then stash_discovery_"
            "result (InjectedState — no JSON copying). Best for frontier "
            "ML / CS preprints not yet citation-tracked."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, stash_discovery_result],
        "model":         model,
    }
