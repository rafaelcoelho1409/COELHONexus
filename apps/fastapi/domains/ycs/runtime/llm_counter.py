"""Per-extract-run LLM call/token counters for YCS's Neo4j entity
extraction — Redis-backed, same contract/shape as DD's
`domains.dd.runtime.llm_counter` (ported deliberately, not
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

from langchain_core.callbacks import AsyncCallbackHandler

logger = logging.getLogger(__name__)


_extract_id_var: ContextVar[str | None] = ContextVar(
    "ycs_llm_extract_id", default=None,
)
_video_id_var: ContextVar[str | None] = ContextVar(
    "ycs_llm_video_id", default=None,
)

_COUNTER_TTL_S = 24 * 60 * 60  # matches PIPELINE_STATE_TTL_S


def set_context(*, extract_id: str | None, video_id: str | None) -> None:
    """Set the current YCS LLM attribution context for this asyncio
    Task. Safe under concurrent extraction — `contextvars` isolate per
    Task, not just per coroutine function, so N concurrently-running
    `_extract_one` calls each keep their own video_id."""
    _extract_id_var.set(extract_id)
    _video_id_var.set(video_id)


def clear_context() -> None:
    set_context(extract_id=None, video_id=None)


def get_context() -> tuple[str | None, str | None]:
    return _extract_id_var.get(), _video_id_var.get()


def _counters_key(extract_id: str) -> str:
    return f"ycs:{extract_id}:llm:counters"


def _models_key(extract_id: str, video_id: str) -> str:
    return f"ycs:{extract_id}:llm:models:{video_id}"


async def bump_current_call(
    *,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int,
    model: str,
) -> None:
    """Bump counters for the current YCS context; no-op outside one
    (e.g. a caller that never set_context, or a call that raced past
    clear_context — safe to just skip rather than mis-attribute)."""
    extract_id, video_id = get_context()
    if not extract_id or not video_id:
        return
    # Deferred import — pipeline_task's package __init__ drags in
    # infra.celery, which needs real env vars at import time (see
    # graph_builder/service.py's redis_client for the same reasoning).
    from domains.ycs.pipeline_task.streaming import build_redis_client

    redis = build_redis_client()
    try:
        counters_k = _counters_key(extract_id)
        models_k = _models_key(extract_id, video_id)
        pp = f"node:{video_id}"
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
        pipe.expire(counters_k, _COUNTER_TTL_S)
        pipe.expire(models_k, _COUNTER_TTL_S)
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
    frontend's `llm_totals.js` renders either with zero changes."""
    empty: dict[str, Any] = {
        "extract_id": extract_id,
        "total": {
            "calls": 0, "tokens_in": 0, "tokens_out": 0, "reasoning_tokens": 0,
        },
        "by_node": {},
    }
    if not extract_id:
        return empty

    from domains.ycs.pipeline_task.streaming import build_redis_client

    redis = build_redis_client()
    try:
        raw = await redis.hgetall(_counters_key(extract_id))
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
                _, video_id, key = field.split(":", 2)
            except ValueError:
                continue
            node_fields.setdefault(video_id, {})[key] = int(val or 0)

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
    raw = await redis.hgetall(_models_key(extract_id, video_id))
    by_model: dict[str, dict[str, int]] = {}
    for k, v in (raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        try:
            model, field = key.rsplit(":", 1)
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
        try:
            llm_output = getattr(response, "llm_output", None) or {}
            usage = (
                llm_output.get("token_usage")
                or llm_output.get("usage")
                or {}
            )
            tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
            tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            details = (
                usage.get("completion_tokens_details")
                or usage.get("output_tokens_details")
                or {}
            )
            if not isinstance(details, dict):
                details = {}
            reasoning = int(usage.get("reasoning_tokens") or details.get("reasoning_tokens") or 0)
            model = (
                llm_output.get("model_name")
                or llm_output.get("model")
                or "unknown"
            )
            await bump_current_call(
                tokens_in=tokens_in, tokens_out=tokens_out,
                reasoning_tokens=reasoning, model=model,
            )
        except Exception as e:
            logger.warning(f"[ycs-llm-counter] on_llm_end failed: {type(e).__name__}: {e}")
