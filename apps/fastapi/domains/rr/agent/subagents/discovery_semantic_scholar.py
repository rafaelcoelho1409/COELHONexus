"""Semantic Scholar discovery subagent (active in SUBAGENTS mode).

See discovery_arxiv.py for the pattern. This subagent holds the
`semantic_scholar_search` MCP tool + `stash_discovery_result` and
shares the `rotator_etiquette` skill.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_DISCOVERY_S2, TOOL_S2_SEARCH
from ..mcp_client import get_tools_by_name
from ..prompts import DISCOVERY_S2_SYSTEM_PROMPT
from ..skills import SKILL_ROTATOR_ETIQUETTE
from ..tools.fs_tools import stash_discovery_result


async def build_discovery_semantic_scholar(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the Semantic Scholar discovery worker."""
    mcp_tools = await get_tools_by_name(TOOL_S2_SEARCH)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{DISCOVERY_S2_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_DISCOVERY_S2,
        "description": (
            "Searches Semantic Scholar for papers with citation-graph + "
            "influence signals (citations, influential_citation_count, "
            "tldr). Calls semantic_scholar_search MCP + stash_discovery_"
            "result (InjectedState). Optionally uses BYOK SEMANTIC_SCHOLAR_"
            "API_KEY for higher RPS."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, stash_discovery_result],
        "model":         model,
    }
