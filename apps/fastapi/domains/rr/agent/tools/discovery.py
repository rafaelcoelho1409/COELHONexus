"""Deterministic discovery tools — orchestrator-level Python @tool wrappers
around the 4 source MCP tools.

REPLACES the LLM-driven discovery subagents (`subagents/discovery_*.py`).
Those subagents had to copy the MCP tool's 5KB JSON output character-perfect
into `stash_discovery_result(papers_json="...")` — 17-120B models all
eventually garbled it, producing repeated retry loops.

New flow:
  orchestrator → discover_arxiv(scan_id, query, n_max)
    → calls arxiv_search MCP tool directly (Python, no LLM)
    → parses the returned paper list
    → fs_write(scan_id, 'discovery/arxiv.json', papers)
    → returns short string summary (e.g. "wrote 16 arxiv papers")

Net effect:
  - JSON truncation failure mode eliminated entirely (no LLM transcription)
  - LLM calls per discovery phase: 4-12 → 1 (orchestrator's single dispatch)
  - Discovery phase wall-time: 10 min → 30 sec
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from ..keys import (
    TOOL_ARXIV_SEARCH,
    TOOL_HF_DAILY,
    TOOL_HN_SEARCH,
    TOOL_S2_SEARCH,
    fs_discovery_path,
)
from ..mcp_client import get_tools_by_name
from .state import fs_write


logger = logging.getLogger(__name__)


# Robust MCP result parser — langchain-mcp-adapters returns the tool result
# in several shapes depending on adapter version + tool. Handle all:
#   - list[dict]            (already-parsed paper records)
#   - str                   (JSON-encoded list)
#   - list[TextContent]     ([{"type":"text", "text":"<JSON>"}, ...])
#   - dict with "text" key  ({"type":"text", "text":"<JSON>"})
def _parse_mcp_result(result: Any) -> list[dict]:
    """Best-effort extraction of paper list[dict] from any MCP return shape."""
    if result is None:
        return []
    # Direct list of paper dicts
    if isinstance(result, list) and result and isinstance(result[0], dict) \
       and "type" not in result[0]:
        return [p for p in result if isinstance(p, dict)]
    # List of TextContent blocks
    if isinstance(result, list):
        texts: list[str] = []
        for block in result:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    texts.append(t)
        combined = "".join(texts)
        if combined:
            try:
                data = json.loads(combined)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass
        return []
    # Single JSON string
    if isinstance(result, str):
        try:
            data = json.loads(result)
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []
    # Wrapped TextContent dict
    if isinstance(result, dict):
        if "text" in result:
            try:
                data = json.loads(result["text"])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                return []
        if "papers" in result and isinstance(result["papers"], list):
            return result["papers"]
    return []


async def _call_mcp_safely(
    tool_name: str, args: dict[str, Any], *, scan_id: str, source: str,
) -> list[dict]:
    """Invoke an MCP tool. Returns empty list on ANY failure so the orchestrator
    can proceed (triage tolerates per-source zeros).

    Schema note: each FastMCP tool's signature is FLAT, e.g.
        async def arxiv_search(ctx: Context, query: str, n_max: int = 20, ...)
    so the LangChain-adapted BaseTool expects the args dict directly. (Flattened
    Any LLM tool-call shape — wrapped OR flat — Just Works at the
    server boundary; previously `input: SearchInput` confused LiteLLM-routed
    discovery subagents into sending flat args that FastMCP rejected.)
    """
    try:
        tools = await get_tools_by_name(tool_name)
        if not tools:
            logger.error(
                f"[fs-tool] discover_{source} scan_id={scan_id}: "
                f"MCP tool {tool_name!r} not found"
            )
            return []
        result = await tools[0].ainvoke(args)
        papers = _parse_mcp_result(result)
        if not papers:
            logger.warning(
                f"[fs-tool] discover_{source} scan_id={scan_id}: parsed 0 papers "
                f"from MCP result (type={type(result).__name__}, "
                f"sample={str(result)[:200]!r})"
            )
        return papers
    except Exception as e:
        logger.error(
            f"[fs-tool] discover_{source} scan_id={scan_id} MCP call failed: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )
        return []


# 4 orchestrator-level discovery tools — one per source.
# Schema rules for each:
#   - First arg `scan_id` is always required (partitions the fs).
#   - Other args mirror each source's MCP SearchInput shape.
#   - Sensible defaults so the orchestrator can call with minimal args.
@tool
async def discover_arxiv(
    scan_id: str,
    query: str,
    n_max: int = 30,
    sort_by: str = "submittedDate",
    categories: list[str] | None = None,
) -> str:
    """Discover arxiv papers via the arxiv_search MCP tool and stash to fs.

    Args:
        scan_id: Identifier for this radar scan (from your initial user message).
        query: The topical phrase (2-5 words), e.g. 'deep agents'.
        n_max: Max papers to return (1-100). Default 30.
        sort_by: 'submittedDate' (newest first, default) or 'relevance'.
        categories: Optional arxiv categories, e.g. ['cs.LG', 'cs.AI'].
    """
    args: dict[str, Any] = {"query": query, "n_max": n_max, "sort_by": sort_by}
    if categories:
        args["categories"] = categories
    papers = await _call_mcp_safely(
        TOOL_ARXIV_SEARCH, args, scan_id=scan_id, source="arxiv"
    )
    path = fs_discovery_path("arxiv")
    fs_write(scan_id, path, papers)
    logger.info(
        f"[fs-tool] discover_arxiv scan_id={scan_id} count={len(papers)} path={path}"
    )
    return f"wrote {len(papers)} arxiv papers to {path}"


@tool
async def discover_semantic_scholar(
    scan_id: str,
    query: str,
    n_max: int = 30,
    year_min: int | None = None,
    fields_of_study: list[str] | None = None,
) -> str:
    """Discover Semantic Scholar papers via semantic_scholar_search MCP tool.

    Args:
        scan_id: Identifier for this radar scan.
        query: The topical phrase.
        n_max: Max papers (1-100). Default 30.
        year_min: Earliest publication year. Default None (no year filter).
        fields_of_study: Optional, e.g. ['Computer Science'].
    """
    args: dict[str, Any] = {"query": query, "n_max": n_max}
    if year_min is not None:
        args["year_min"] = year_min
    if fields_of_study:
        args["fields_of_study"] = fields_of_study
    papers = await _call_mcp_safely(
        TOOL_S2_SEARCH, args, scan_id=scan_id, source="semantic_scholar"
    )
    path = fs_discovery_path("semantic_scholar")
    fs_write(scan_id, path, papers)
    logger.info(
        f"[fs-tool] discover_semantic_scholar scan_id={scan_id} "
        f"count={len(papers)} path={path}"
    )
    return f"wrote {len(papers)} semantic_scholar papers to {path}"


@tool
async def discover_huggingface_daily_papers(
    scan_id: str,
    n_max: int = 20,
    target_date: str | None = None,
    min_upvotes: int | None = None,
) -> str:
    """Discover today's HuggingFace Daily Papers via huggingface_daily_papers MCP tool.

    The HF feed is date-axis, not text-search — no `query` parameter.

    Args:
        scan_id: Identifier for this radar scan.
        n_max: Max papers (1-50). Default 20.
        target_date: ISO date (YYYY-MM-DD) of the daily curation to fetch.
            Default: server-side today (UTC).
        min_upvotes: Drop papers under this upvote threshold. Default None.
    """
    args: dict[str, Any] = {"n_max": n_max}
    if target_date:
        args["target_date"] = target_date
    if min_upvotes is not None:
        args["min_upvotes"] = min_upvotes
    papers = await _call_mcp_safely(
        TOOL_HF_DAILY, args, scan_id=scan_id, source="huggingface_daily_papers"
    )
    path = fs_discovery_path("huggingface_daily_papers")
    fs_write(scan_id, path, papers)
    logger.info(
        f"[fs-tool] discover_huggingface_daily_papers scan_id={scan_id} "
        f"count={len(papers)} path={path}"
    )
    return f"wrote {len(papers)} hf_daily_papers to {path}"


@tool
async def discover_hn(
    scan_id: str,
    query: str,
    n_max: int = 50,
    min_points: int | None = 50,
    sort_by: str = "relevance",
) -> str:
    """Discover Hacker News stories via the hn_search MCP tool.

    Args:
        scan_id: Identifier for this radar scan.
        query: The topical phrase.
        n_max: Max hits (1-100). Default 50 (HN signal density is lower).
        min_points: Drop stories under this point threshold. Default 50.
        sort_by: 'relevance' (default) or 'date'.
    """
    args: dict[str, Any] = {
        "query": query, "n_max": n_max, "sort_by": sort_by, "tags": ["story"],
    }
    if min_points is not None:
        args["min_points"] = min_points
    papers = await _call_mcp_safely(
        TOOL_HN_SEARCH, args, scan_id=scan_id, source="hn"
    )
    path = fs_discovery_path("hn")
    fs_write(scan_id, path, papers)
    logger.info(
        f"[fs-tool] discover_hn scan_id={scan_id} count={len(papers)} path={path}"
    )
    return f"wrote {len(papers)} hn stories to {path}"
