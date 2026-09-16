"""ycs/rag — Parallel free keyless MCP web search.

2026-09-16: used ONLY by `standard/nodes/fallback_answer` — the CRAG
graceful-degradation branch that already fires when retrieve → grade →
rewrite exhausts retries with zero corpus evidence surviving the
strict grader (see that node's docstring). Deliberately NOT wired into
`SmartRetriever`'s main fan-out (Qdrant + Neo4j + ES): the primary RAG
path's citations stay strictly grounded in indexed video transcripts,
and a 4th arm would just compete with Neo4j/ES for the same `top_k`
slots without fixing anything (see the 2026-09-16 citations-sidebar
investigation — the corpus is small enough that Qdrant alone already
saturates the final rerank cut).

Connects to Parallel's remote, KEYLESS MCP endpoint
(https://search.parallel.ai/mcp) via `langchain-mcp-adapters` —
deliberately NOT the official `langchain-parallel` pip package, which
hard-requires a paid `PARALLEL_API_KEY` (verified via /sota-search:
docs.langchain.com's integration page shows no keyless path). The MCP
endpoint needs no account/key and is "free by default... for hobby use
and personal agents" per Parallel's own announcement — no published
hard rate-limit number, so every call here is short-timeout,
best-effort, and degrades to "no web context" on ANY failure. This
path only fires on the rare corpus-miss branch, so even a full outage
here never blocks a real answer — `fallback_answer` already has its
own non-web soft-evidence + history + parametric-knowledge rescue."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PARALLEL_MCP_URL = "https://search.parallel.ai/mcp"

# Short and separate from `fallback_answer`'s own 60s generation
# budget — this call happens BEFORE that one, on the pipeline's
# already-slowest, worst-UX path. A failed/slow search must not turn
# a 60s rescue into a 90s+ one; see that node's docstring for why its
# own timeout was tightened 90 -> 60s for the same reason.
_SEARCH_TIMEOUT_S = 15.0
_MAX_RESULTS = 5
_EXCERPT_CHAR_CAP = 400

_client: Any = None
_tool: Any = None
_client_loop: asyncio.AbstractEventLoop | None = None

# Event-loop-aware cache — same pattern as `domains/rr/agent/
# mcp_client.py`'s `get_mcp_tools()`: the client binds to whichever
# loop opened it. FastAPI's uvicorn loop is long-lived for this call
# site (fallback_answer only ever runs inside a request, never a
# Celery task), so this normally never re-opens — kept anyway so this
# module stays safe to reuse from any future caller with different
# execution semantics, without silently resolving a stale connection
# against a closed loop.


async def _get_search_tool() -> Any | None:
    """Lazily build + cache the MCP client and its search tool. Returns
    `None` on any init failure — the caller degrades to no web context
    rather than raising; a fresh attempt happens next time the loop
    changes (e.g. process restart), not on every call, so a persistent
    outage doesn't retry-storm the endpoint."""
    global _client, _tool, _client_loop
    current_loop = asyncio.get_running_loop()
    if _tool is not None and _client_loop is current_loop:
        return _tool
    if _client is not None and _client_loop is not current_loop:
        logger.info(
            "[ycs:web_search] event loop changed; dropping stale "
            "Parallel MCP client and re-connecting"
        )
        _client = None
        _tool = None
        _client_loop = None
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        _client = MultiServerMCPClient({
            "parallel": {
                "url":       _PARALLEL_MCP_URL,
                "transport": "streamable_http",
            },
        })
        tools = await asyncio.wait_for(
            _client.get_tools(), timeout = _SEARCH_TIMEOUT_S,
        )
        # 2026-09-16: live-verified against the real endpoint — it
        # serves TWO tools (`web_search`, `web_fetch`); look up by
        # name rather than trusting list order/position.
        by_name = {t.name: t for t in tools}
        if "web_search" not in by_name:
            raise RuntimeError(
                f"Parallel MCP server has no 'web_search' tool "
                f"(got: {list(by_name)})"
            )
        _tool = by_name["web_search"]
        _client_loop = current_loop
        logger.info(
            f"[ycs:web_search] connected to Parallel MCP — tool "
            f"{_tool.name!r} ready"
        )
    except Exception as e:
        _client = None
        _tool = None
        _client_loop = None
        logger.warning(
            f"[ycs:web_search] Parallel MCP init failed — web "
            f"fallback unavailable this call: {type(e).__name__}: {e}"
        )
        return None
    return _tool


def _extract_result_items(raw: Any) -> list[dict]:
    """Normalize whatever `langchain-mcp-adapters` hands back into a
    flat list of result dicts. Live-verified shape (2026-09-16,
    against the real `https://search.parallel.ai/mcp` endpoint):
    `tool.ainvoke()` returns `list[{"type": "text", "text": "<JSON
    string>"}]` (standard MCP text-content-block wrapping); the JSON
    string itself is `{"search_id": ..., "results": [{"url", "title",
    "publish_date", "excerpts": [...]}]}`.

    Stays defensive beyond that one confirmed shape — a raw string, a
    bare dict, or a differently-shaped list all degrade to SOMETHING
    usable rather than raising; only a hard structural mismatch
    ultimately returns []."""
    if not raw:
        return []
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "text" in raw[0]:
        texts = [b.get("text", "") for b in raw if isinstance(b, dict)]
        raw = "\n".join(t for t in texts if t)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return [{"excerpts": [raw]}] if raw.strip() else []
    if isinstance(raw, dict):
        raw = raw.get("results") or raw.get("data") or [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return [{"excerpts": [str(raw)]}]


def _format_results(raw: Any) -> str:
    """Render up to `_MAX_RESULTS` result items into a short text
    block for the fallback prompt's `{web_context}` slot."""
    items = _extract_result_items(raw)
    if not items:
        return ""
    parts: list[str] = []
    for item in items[:_MAX_RESULTS]:
        title = item.get("title") or item.get("name") or ""
        url = item.get("url") or item.get("link") or ""
        excerpts = item.get("excerpts")
        if isinstance(excerpts, list):
            excerpt = " ".join(str(e) for e in excerpts if e)
        else:
            excerpt = str(
                item.get("excerpt") or item.get("snippet")
                or item.get("content") or item.get("text") or excerpts or ""
            )
        excerpt = excerpt[:_EXCERPT_CHAR_CAP]
        header = f"[{title}]({url})" if (title or url) else ""
        block = f"{header}\n{excerpt}".strip()
        if block:
            parts.append(block)
    return "\n\n---\n\n".join(parts)


async def search_web(question: str, session_id: str | None = None) -> str:
    """Best-effort web search for `fallback_answer`'s `{web_context}`
    slot. Returns `""` on ANY failure (missing dependency, network,
    rate limit, timeout, unrecognized response shape) — the caller
    must treat empty as "no web context available", never raise.

    `session_id` — live-verified against the real tool schema: the
    endpoint asks for "a stable identifier... reused on every call...
    used for free-tier rate limiting." Pass the YCS conversation
    `thread_id` so one Ask thread maps to one Parallel session instead
    of a fresh anonymous bucket per call (`None` still works — the
    caller/tool falls back on its own default bucketing)."""
    if not question or not question.strip():
        return ""
    try:
        tool = await _get_search_tool()
        if tool is None:
            return ""
        args: dict[str, Any] = {
            # ONE query, not the "3-6 words, 2-3 queries" ideal the
            # tool description recommends — this is a best-effort
            # fallback path, not worth a separate keyword-extraction
            # step for now; the full question still searches fine.
            "search_queries": [question],
            "objective":      question,
        }
        if session_id:
            args["session_id"] = session_id[:100]  # tool's own maxLength
        raw = await asyncio.wait_for(
            tool.ainvoke(args), timeout = _SEARCH_TIMEOUT_S,
        )
        return _format_results(raw)
    except Exception as e:
        logger.warning(
            f"[ycs:web_search] search failed for {question[:80]!r}: "
            f"{type(e).__name__}: {e}"
        )
        return ""
