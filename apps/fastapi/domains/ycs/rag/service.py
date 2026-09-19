"""ycs/rag — resilient single-LLM-call helpers + the Parallel free
keyless MCP web-search fallback, shared by both the standard and
adaptive graphs.

`resilient_ainvoke`/`hedged_ainvoke` mirror the Docs Distiller Planner
engine (`doc_distill` + `chat_judge_bandit_async`): retry only
genuinely transient errors (timeout/connection — never rate_limit,
which means the provider side is benched and a retry just burns
budget), with jittered backoff so concurrent retries don't herd onto
the same arm.

Structure here is one layer simpler than DD's on purpose: every Ask
node already wraps its call in `asyncio.wait_for(..., timeout=node_timeout)`
where `node_timeout` (30-600s) sits well under the SDK client's own
600s ceiling — so that wait_for IS the hard backstop over the SDK
timeout (the httpx-gap fix: httpx read-timeout measures inter-chunk
gaps, not total call time; a trickling or silently-stalled connection
outlives it without raising, but it cannot outlive this wait_for). This
module adds the missing piece DD has and Ask lacked: transparent
retries *inside* that bound.

Drop-in contract: `resilient_ainvoke(chain, payload, ...)` raises the
LAST exception unchanged after exhausting attempts — so each node's
existing `except (asyncio.TimeoutError, Exception)` handling behaves
exactly as today, only now transient blips get retried first instead
of surfacing immediately. Non-transient errors (validation, auth,
rate_limit, parse) raise on the first attempt with zero extra waiting.

`search_web` connects to Parallel's remote, KEYLESS MCP endpoint
(https://search.parallel.ai/mcp) via `langchain-mcp-adapters` —
deliberately NOT the official `langchain-parallel` pip package, which
hard-requires a paid `PARALLEL_API_KEY` (verified via /sota-search:
docs.langchain.com's integration page shows no keyless path). Used
ONLY by `standard/nodes/fallback_answer` — the CRAG graceful-
degradation branch that already fires when retrieve → grade → rewrite
exhausts retries with zero corpus evidence surviving the strict
grader. Every call is short-timeout, best-effort, and degrades to "no
web context" on ANY failure."""
from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import domains

from . import domain, params


logger = logging.getLogger(__name__)


async def capture_llm_usage(response: object) -> None:
    """2026-09-16 fix: every Ask node calls `resilient_ainvoke`/
    `hedged_ainvoke`, but neither ever read the response's token usage
    — `set_node()` (called by every node before invoking) only tags
    WHICH node is active; nothing was actually bumping the counter, so
    `/agents/usage/{thread_id}` silently stayed empty for the whole
    Ask graph (only Neo4j extraction's separate `LLMGraphTransformer`
    callback path worked). `app.state.llm`/`llm_fast` are plain
    `ChatOpenAI` (`domains/llm/rotator/chain/service.py`) — LangChain
    populates `AIMessage.usage_metadata` from any OpenAI-compatible
    `usage` response field automatically, no rotator-specific parsing
    needed. Best-effort: a malformed/missing usage block must never
    fail the caller's real answer."""
    try:
        usage = getattr(response, "usage_metadata", None) or {}
        tokens_in  = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        if not (tokens_in or tokens_out):
            return
        details = usage.get("output_token_details") or {}
        reasoning = int(
            (details.get("reasoning") if isinstance(details, dict) else 0) or 0
        )
        meta = getattr(response, "response_metadata", None) or {}
        model = meta.get("model_name") or meta.get("model") or "unknown"
        await domains.ycs.runtime.llm_counter.service.bump_current_call(
            tokens_in = tokens_in, tokens_out = tokens_out,
            reasoning_tokens = reasoning, model = model,
        )
    except Exception as e:
        logger.warning(
            f"[ycs:rag:usage] capture failed: {type(e).__name__}: {e}"
        )


async def resilient_ainvoke(
    chain,
    payload: dict,
    *,
    operation:    str,
    timeout_s:    float,
    max_attempts: int             = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """`await asyncio.wait_for(chain.ainvoke(payload), timeout_s)` with
    transient-only retries inside the bound. Raises the last exception
    unchanged when attempts run out (or immediately for non-transient)."""
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = await asyncio.wait_for(
                chain.ainvoke(payload),
                timeout = timeout_s,
            )
            await capture_llm_usage(result)
            return result
        except Exception as e:  # noqa: BLE001 — classified below
            last_exc = e
            if not domain.is_transient(e) or attempt >= max_attempts:
                break
            delay = backoff_s[min(attempt - 1, len(backoff_s) - 1)]
            delay *= 1.0 + random.random() * 0.2  # DD-parity jitter
            logger.warning(
                f"[ycs:rag:{operation}] transient "
                f"{type(e).__name__} (attempt {attempt}/{max_attempts}) — "
                f"retrying in {delay:.1f}s",
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


async def hedged_ainvoke(
    chain,
    payload: dict,
    *,
    operation:    str,
    timeout_s:    float,
    hedge_after_s: float = 20.0,
    max_invokes:  int   = 3,
    backoff_s:    tuple[float, ...] = (2.0, 5.0),
):
    """Tail-cutting racer for CHEAP calls (Tail-at-Scale pattern).

    Fires the primary immediately plus ONE delayed duplicate; slow tail
    (> `hedge_after_s`) gets raced, first success wins, loser cancelled
    — healthy path pays zero extra. A fast transient failure fires the
    duplicate immediately (retry semantics); non-transient raises at
    once. Total invokes capped at `max_invokes`, everything bounded by
    `timeout_s` from entry. Raises the last exception unchanged (same
    drop-in contract as `resilient_ainvoke`).

    Cost rule: only use where one extra short completion is affordable
    (fast-mode definitions). NEVER for heavy generate/synthesize calls —
    duplicating 12k-context completions is real money for unproven gain.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    fire_now = asyncio.Event()
    invokes = 0
    last_exc: BaseException | None = None

    async def _one() -> object:
        nonlocal invokes
        invokes += 1
        result = await chain.ainvoke(payload)
        await capture_llm_usage(result)
        return result

    async def _delayed() -> object:
        try:
            await asyncio.wait_for(fire_now.wait(), timeout = hedge_after_s)
        except asyncio.TimeoutError:
            pass
        return await _one()

    async def _backoff(attempt: int) -> None:
        delay = backoff_s[min(attempt, len(backoff_s) - 1)]
        delay *= 1.0 + random.random() * 0.2
        logger.warning(
            f"[ycs:rag:{operation}] transient slow/fail — hedge "
            f"armed (invoke {invokes}/{max_invokes})"
        )
        await asyncio.sleep(delay)

    pending: set[asyncio.Task] = set()
    try:
        pending.add(asyncio.ensure_future(_one()))
        pending.add(asyncio.ensure_future(_delayed()))
        attempt = 0
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(
                    f"[ycs:rag:{operation}] {timeout_s}s bound exceeded"
                )
            done, pending = await asyncio.wait(
                pending, timeout = remaining,
                return_when = asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError(
                    f"[ycs:rag:{operation}] {timeout_s}s bound exceeded"
                )
            for task in done:
                try:
                    result = task.result()
                except Exception as e:  # noqa: BLE001 — classified below
                    last_exc = e
                    if not domain.is_transient(e):
                        for p in pending:
                            p.cancel()
                        raise
                    # Transient: fire the duplicate NOW (retry semantics)
                    # if budget remains, else keep waiting for the other.
                    fire_now.set()
                    if invokes < max_invokes and not pending:
                        await _backoff(attempt)
                        attempt += 1
                        pending.add(asyncio.ensure_future(_one()))
                    continue
                for p in pending:
                    p.cancel()
                return result
        assert last_exc is not None
        raise last_exc
    finally:
        fire_now.set()
        for task in pending:
            task.cancel()


# Parallel MCP web-search fallback
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
                "url":       params.PARALLEL_MCP_URL,
                "transport": "streamable_http",
            },
        })
        tools = await asyncio.wait_for(
            _client.get_tools(), timeout = params.SEARCH_TIMEOUT_S,
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
            tool.ainvoke(args), timeout = params.SEARCH_TIMEOUT_S,
        )
        return domain.format_web_search_results(raw)
    except Exception as e:
        logger.warning(
            f"[ycs:web_search] search failed for {question[:80]!r}: "
            f"{type(e).__name__}: {e}"
        )
        return ""
