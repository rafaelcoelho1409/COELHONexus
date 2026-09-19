"""Per-extract-run LLM call/token counters for YCS's Neo4j entity
extraction — Redis-backed, same contract/shape as DD's
`domains.dd.runtime.service` (ported deliberately, not
reinvented) so the FastHTML LLM-usage drawer (`static/js/dd/shared/
llm_totals.js`) can render YCS's data with the same rendering code.

Key difference from DD: DD's raw-`AsyncOpenAI` hot path gets a real
provider response object (`.usage`, `.model`) back from every call, so
`bump_current_call` there just reads it directly. YCS's Neo4j
extraction goes through LangChain's `LLMGraphTransformer`, which never
surfaces the raw response to its caller — so usage capture here goes
through a LangChain callback (`YCSLLMUsageCallback` below) attached to
the LLM via `.with_config(callbacks=[...])`, firing on `on_llm_end`
instead of being read from a return value.

"node_id" in the shared `{total, by_node}` shape is the video_id being
extracted (DD's "node" is a LangGraph node/chapter; YCS has no
distinct workflow stages here — one call type, so grouping by which
video the call was for is the useful breakdown instead)."""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

import domains
from langchain_core.callbacks import AsyncCallbackHandler

from . import domain, keys, params


logger = logging.getLogger(__name__)


_extract_id_var: ContextVar[str | None] = ContextVar(
    "ycs_llm_extract_id", default=None,
)
_video_id_var:  ContextVar[str | None] = ContextVar(
    "ycs_llm_video_id", default=None,
)
# 2026-09-15: Ask path re-uses this counter with a fake "thread" id as
# the channel — one conversation's full LLM bill under one counter.
_thread_id_var: ContextVar[str | None] = ContextVar(
    "ycs_llm_thread_id", default=None,
)
_node_var: ContextVar[str | None] = ContextVar(
    "ycs_llm_node", default=None,
)


def set_context(*, extract_id: str | None, video_id: str | None) -> None:
    """Set the current YCS LLM attribution context for this asyncio
    Task. Safe under concurrent extraction — `contextvars` isolate per
    Task, not just per coroutine function, so N concurrently-running
    `_extract_one` calls each keep their own video_id."""
    _extract_id_var.set(extract_id)
    _video_id_var.set(video_id)


def set_thread(*, thread_id: str | None) -> None:
    """2026-09-15: per-request attribution key for the Ask path —
    threads the conversation's aggregate under one counter (vs an
    extract_id/video_id pair in ingestion). Namespace: a thread never
    sees a video's `video_id` too, so buckets don't collide."""
    _thread_id_var.set(thread_id)


def set_node(*, node: str | None) -> None:
    """Which node in the Ask graph is calling (generate/synthesize/
    classify/critic/…). The per-thread counter splits by this so
    `/agents/usage/{thread_id}` can show a per-node breakdown like
    Ingestion's per-video drawer does."""
    _node_var.set(node)


def clear_context() -> None:
    set_context(extract_id=None, video_id=None)


def get_context() -> tuple[str | None, str | None]:
    return _extract_id_var.get(), _video_id_var.get()


def get_thread_state() -> tuple[str | None, str | None]:
    return _thread_id_var.get(), _node_var.get()


def clear_state() -> None:
    """Reset ALL four vars — safe default for a request boundary."""
    set_context(extract_id=None, video_id=None)
    set_thread(thread_id=None)
    set_node(node=None)


async def bump_current_call(
    *,
    tokens_in:    int,
    tokens_out:   int,
    reasoning_tokens: int,
    model:        str,
) -> None:
    """Bump counters for the current YCS context; no-op outside one
    (e.g. a caller that never set_context, or a call that raced past
    clear_context — safe to just skip rather than mis-attribute).

    2026-09-15: a `thread_id` context (from `set_thread`) takes the Ask
    path's aggregate — `ycs:{thread_id}` keys instead of
    `ycs:{extract_id}` — so one conversation's total bill sits under
    one Redis counter regardless of which nodes ran inside it. Nodes
    WITHOUT a thread fall back to the original extract/video path."""
    thread_id, node = get_thread_state()
    extract_id, video_id = None, None
    if not thread_id:
        extract_id, video_id = get_context()
    if not (thread_id or (extract_id and video_id)):
        return
    redis = domains.ycs.pipeline_task.service.build_redis_client()
    try:
        if thread_id:
            counters_k = keys.counters_key(thread_id)
            models_k   = keys.models_key(thread_id, node or "unknown")
            pp         = f"node:{node or 'unknown'}"
        else:
            counters_k = keys.counters_key(extract_id or "")
            models_k   = keys.models_key(extract_id or "", video_id or "")
            pp         = f"node:{video_id or 'unknown'}"
        pipe = redis.pipeline(transaction=False)
        pipe.hincrby(counters_k, f"{pp}:calls", 1)
        pipe.hincrby(counters_k, f"{pp}:tokens_in", int(tokens_in))
        pipe.hincrby(counters_k, f"{pp}:tokens_out", int(tokens_out))
        pipe.hincrby(counters_k, f"{pp}:reasoning_tokens", int(reasoning_tokens))
        pipe.hincrby(counters_k, "total:calls", 1)
        pipe.hincrby(counters_k, "total:tokens_in", int(tokens_in))
        pipe.hincrby(counters_k, "total:tokens_out", int(tokens_out))
        pipe.hincrby(counters_k, "total:reasoning_tokens", int(reasoning_tokens))
        pipe.hincrby(models_k, f"{model}:calls", 1)
        pipe.hincrby(models_k, f"{model}:tokens_in", int(tokens_in))
        pipe.hincrby(models_k, f"{model}:tokens_out", int(tokens_out))
        pipe.hincrby(models_k, f"{model}:reasoning_tokens", int(reasoning_tokens))
        pipe.expire(counters_k, params.COUNTER_TTL_S)
        pipe.expire(models_k, params.COUNTER_TTL_S)
        await pipe.execute()
    except Exception as e:
        logger.warning(
            f"[ycs-llm-counter] bump failed extract_id={extract_id} "
            f"video_id={video_id} model={model}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            await redis.close()
        except Exception:
            pass


async def read_counters(extract_id: str) -> dict[str, Any]:
    """Same `{total, by_node}` shape DD's `read_counters` returns — the
    frontend's `llm_totals.js` renders either with zero changes.

    2026-09-15: re-tasked for Ask: the input is a THREAD id (via the
    new `set_thread` path), so `/agents/usage/{thread_id}` reports the
    aggregate for one conversation. Ingestion's video-keyed counter is
    untouched — pass an extract_id-shaped string and it works as before."""
    key = keys.counters_key(extract_id)  # unchanged — extract_id IS the key
    empty: dict[str, Any] = {
        "extract_id": extract_id,
        "total": {
            "calls": 0, "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
        },
        "by_node": {},
    }
    if not extract_id:
        return empty

    redis = domains.ycs.pipeline_task.service.build_redis_client()
    try:
        raw = await redis.hgetall(key)
        if not raw:
            return empty
        counters = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        out: dict[str, Any] = {
            "extract_id": extract_id,
            "total": {
                "calls": int(counters.get("total:calls", 0) or 0),
                "tokens_in": int(counters.get("total:tokens_in", 0) or 0),
                "tokens_out": int(counters.get("total:tokens_out", 0) or 0),
                "reasoning_tokens": int(counters.get("total:reasoning_tokens", 0) or 0),
            },
            "by_node": {},
        }
        node_fields: dict[str, dict[str, int]] = {}
        for field, val in counters.items():
            if not field.startswith("node:"):
                continue
            try:
                _, video_id, key_ = field.split(":", 2)
            except ValueError:
                continue
            node_fields.setdefault(video_id, {})[key_] = int(val or 0)

        for video_id, fields in node_fields.items():
            by_model = await _read_models(redis, extract_id, video_id)
            out["by_node"][video_id] = {
                "calls": int(fields.get("calls", 0)),
                "tokens_in": int(fields.get("tokens_in", 0)),
                "tokens_out": int(fields.get("tokens_out", 0)),
                "reasoning_tokens": int(fields.get("reasoning_tokens", 0)),
                "by_model": by_model,
            }
        return out
    except Exception as e:
        logger.warning(f"[ycs-llm-counter] read failed extract_id={extract_id}: {e}")
        return empty
    finally:
        try:
            await redis.close()
        except Exception:
            pass


async def _read_models(redis: Any, extract_id: str, video_id: str) -> dict:
    raw = await redis.hgetall(keys.models_key(extract_id, video_id))
    by_model: dict[str, dict[str, int]] = {}
    for k, v in (raw or {}).items():
        key_ = k.decode() if isinstance(k, bytes) else k
        try:
            model, field = key_.rsplit(":", 1)
        except ValueError:
            continue
        by_model.setdefault(model, {})[field] = int(v or 0)
    return by_model


class YCSLLMUsageCallback(AsyncCallbackHandler):
    """LangChain async callback handler — attach via
    `llm.with_config(callbacks=[YCSLLMUsageCallback()])` so it fires
    for every internal call `LLMGraphTransformer` makes through that
    LLM instance, without needing access to its return value (which
    `aconvert_to_graph_documents` never exposes to its caller)."""

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """Fires for every LLM call — LangChain's `with_structured_output`
        goes through this same hook (the wrapper is just a Runnable that
        delegates to the inner `Runnable` — verified in langchain-openai
        1.x). Nodes tag themselves via `set_node` right before invoking,
        so this lands in the right bucket."""
        try:
            parsed = domain.parse_callback_response(response)
            await bump_current_call(
                tokens_in        = parsed["tokens_in"],
                tokens_out       = parsed["tokens_out"],
                reasoning_tokens = parsed["reasoning_tokens"],
                model            = parsed["model"],
            )
        except Exception as e:
            logger.warning(f"[ycs-llm-counter] on_llm_end failed: {type(e).__name__}: {e}")
