"""HuggingFace Daily Papers tool — wraps the curated `/api/daily_papers`
endpoint as an MCP tool, with HF-unique signals (community **upvotes** ·
comment count · discussion link · arxiv_id for cross-source dedup with the
arxiv tool).

NOT a search tool — HF Daily Papers is a CURATED feed; the natural axis is
publication date, not text query. The agent calls this when it wants the
ML community's daily signal that arxiv's firehose can't surface."""
from __future__ import annotations
from . import config, domain, keys, schemas, service, tool

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP


__all__ = ["config", "domain", "keys", "schemas", "service", "tool"]


def register(mcp: FastMCP) -> None:
    """Register the huggingface_daily_papers tool on the root server."""
    tool.register(mcp)
