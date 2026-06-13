"""Hacker News discovery subagent (active in SUBAGENTS mode).

Wraps the `hn_search` MCP tool + `stash_discovery_result`. Picks up the
cross-tier dedup arxiv_id when an HN story's URL points at arxiv.org or
huggingface.co/papers.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_DISCOVERY_HN, TOOL_HN_SEARCH
from ..mcp_client import get_tools_by_name
from ..prompts import DISCOVERY_HN_SYSTEM_PROMPT
from ..skills import SKILL_ROTATOR_ETIQUETTE
from ..tools.fs_tools import stash_discovery_result


async def build_discovery_hn(model: BaseChatModel) -> dict[str, Any]:
    """SubAgent dict for the Hacker News discovery worker."""
    mcp_tools = await get_tools_by_name(TOOL_HN_SEARCH)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{DISCOVERY_HN_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_DISCOVERY_HN,
        "description": (
            "Searches Hacker News via Algolia. Calls hn_search MCP + "
            "stash_discovery_result. Returns Hit records with community "
            "traction (points, num_comments) + extracted arxiv_id when "
            "the story URL points at arxiv.org or HF papers."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, stash_discovery_result],
        "model":         model,
    }
