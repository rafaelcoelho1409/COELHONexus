"""HuggingFace Daily Papers discovery subagent (active in SUBAGENTS mode).

The HF feed is DATE-AXIS, not text-search — no `query` parameter. The
subagent's job: call `huggingface_daily_papers` (server-side defaults
to yesterday UTC), then stash via InjectedState.
"""
from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from ..keys import SUBAGENT_DISCOVERY_HF, TOOL_HF_DAILY
from ..mcp_client import get_tools_by_name
from ..prompts import DISCOVERY_HF_SYSTEM_PROMPT
from ..skills import SKILL_ROTATOR_ETIQUETTE
from ..tools.fs_tools import stash_discovery_result


async def build_discovery_huggingface_daily_papers(
    model: BaseChatModel,
) -> dict[str, Any]:
    """SubAgent dict for the HF Daily Papers discovery worker."""
    mcp_tools = await get_tools_by_name(TOOL_HF_DAILY)
    full_prompt = (
        f"=== SKILL: rotator_etiquette ===\n\n"
        f"{SKILL_ROTATOR_ETIQUETTE}\n\n"
        f"=== ROLE ===\n\n"
        f"{DISCOVERY_HF_SYSTEM_PROMPT}"
    )
    return {
        "name":          SUBAGENT_DISCOVERY_HF,
        "description": (
            "Fetches HuggingFace's curated Daily Papers feed. Date-axis "
            "(not query-axis) — community-filtered notable papers. Calls "
            "huggingface_daily_papers MCP + stash_discovery_result. Carries "
            "arxiv_id always — the cross-source dedup primary lane."
        ),
        "system_prompt": full_prompt,
        "tools":         [*mcp_tools, stash_discovery_result],
        "model":         model,
    }
