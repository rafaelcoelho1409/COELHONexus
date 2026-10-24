"""llm_counter service — per-run LLM call / token counters in Redis (one hash per run, one per node), the usage-drawer
reader, the MinIO snapshot of a finished run and the LangChain usage callback. One module for every bounded context:
a context only sets its attribution (`llm_context`, or `set_context` + `set_node`) and the namespace (`dd`, `ycs`) picks
the keys and the payload shape (`params.NAMESPACES`). Redis serves the live drawer; the DD snapshot in MinIO avoids
live Langfuse queries for a finished run.

Attribution is a `ContextVar`, so concurrent asyncio tasks (YCS extracts N videos at once) each keep their own; a call
made outside any context is not counted rather than mis-attributed. Counting is best-effort: it never raises.

The chat adapter (`settings.chat`) counts each call it makes (`bump_response` / `bump_current_failure`). Calls LangChain
makes where no response reaches the caller (YCS's `LLMGraphTransformer`) are counted by `LLMCounterCallback`, attached
with `llm.with_config(callbacks=[...])`; it also opens the `gen_ai.chat` span and records `gen_ai_call_total` /
`gen_ai_call_duration` for those calls, keyed by LangChain's `run_id` (one instance serves every concurrent extract)."""
from __future__ import annotations
import domains, infra
from . import domain, entities, keys, params

import contextlib
import json
import logging
import time
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler


logger = logging.getLogger(__name__)


_attribution_var: ContextVar[entities.Attribution | None] = ContextVar("llm_counter_attribution", default = None)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def set_context(*, ns: str, run_id: str | None, node_id: str | None = None, stage: str | None = None) -> None:
    """Bill the LLM calls of this async task to `(ns, run_id)`, counted under `node_id`. YCS: an extract run (node =
    video) or an Ask thread (node = graph node, set later by `set_node`). A falsy `run_id` clears the context."""
    _attribution_var.set(entities.Attribution(ns = ns, run_id = run_id, node_id = node_id, stage = stage) if run_id else None)


def set_node(*, node: str | None) -> None:
    """Which node is about to call the LLM — the counter splits by it. A no-op outside a context."""
    current = _attribution_var.get()
    if current is not None:
        _attribution_var.set(replace(current, node_id = node))


def clear_context() -> None:
    _attribution_var.set(None)


def get_context() -> entities.Attribution | None:
    return _attribution_var.get()


@contextlib.contextmanager
def llm_context(*, ns: str, run_id: str, node_id: str, stage: str | None = None) -> Iterator[None]:
    """Bill the LLM calls made inside the block to `(ns, run_id, node_id)`; the previous attribution is restored on exit,
    success or error."""
    previous = _attribution_var.get()
    _attribution_var.set(entities.Attribution(ns = ns, run_id = run_id, node_id = node_id, stage = stage))
    try:
        yield
    finally:
        _attribution_var.set(previous)


def _active() -> entities.Attribution | None:
    attribution = _attribution_var.get()
    return attribution if attribution is not None and attribution.ns and attribution.run_id else None


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------

def _queue_bump(
    pipe: Any, *, counters_k: str, models_k: str, node_id: str, model: str, usage: entities.Usage,
) -> None:
    """Queue one call on `pipe`: run + node + per-model counts, and the TTLs."""
    amounts = {"calls": 1, "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out, "reasoning_tokens": usage.reasoning_tokens}
    for field, amount in amounts.items():
        pipe.hincrby(counters_k, f"node:{node_id}:{field}", amount)
        pipe.hincrby(counters_k, f"total:{field}", amount)
        pipe.hincrby(models_k, f"{model}:{field}", amount)
    pipe.expire(counters_k, params.COUNTER_TTL_S)
    pipe.expire(models_k, params.COUNTER_TTL_S)


async def bump_current_call(*, usage: entities.Usage, model: str) -> None:
    """Count one LLM call on the current attribution; a no-op outside one."""
    attribution = _active()
    if attribution is None:
        return
    node_id = attribution.node_id or params.UNKNOWN_NODE
    try:
        async with infra.redis.service.client() as r:
            counters_k = keys.counters_key(attribution.ns, attribution.run_id)
            pipe = r.pipeline(transaction = False)
            if attribution.stage:
                pipe.hset(counters_k, "stage", attribution.stage)
            _queue_bump(
                pipe, counters_k = counters_k, models_k = keys.models_key(attribution.ns, attribution.run_id, node_id),
                node_id = node_id, model = model, usage = usage,
            )
            await pipe.execute()
    except Exception as e:
        logger.warning(
            f"[llm-counter] bump failed ns={attribution.ns} run={attribution.run_id} node={node_id} model={model}: "
            f"{type(e).__name__}: {e}"
        )


async def bump_response(response: Any, *, deployment: str | None = None) -> None:
    """Count one call from its response (an `AIMessage`, an `LLMResult` or a raw provider response); `deployment` is the
    model the call was sent to, used when the response names none. A no-op outside a context."""
    if _active() is None:
        return
    await bump_current_call(
        usage = domain.extract_usage(response), model = domain.model_from_response(response, deployment),
    )


async def bump_current_failure(exc: BaseException) -> None:
    """Count one failed LLM call on the current attribution, so a run's error rate shows next to its call count. A no-op
    outside a context, and for namespaces that do not show errors."""
    attribution = _active()
    if attribution is None or not params.NAMESPACES.get(attribution.ns, params.DEFAULT_NAMESPACE).errors:
        return
    node_id = attribution.node_id or params.UNKNOWN_NODE
    try:
        async with infra.redis.service.client() as r:
            counters_k = keys.counters_key(attribution.ns, attribution.run_id)
            pipe = r.pipeline(transaction = False)
            if attribution.stage:
                pipe.hset(counters_k, "stage", attribution.stage)
            pipe.hincrby(counters_k, f"node:{node_id}:errors", 1)
            pipe.hincrby(counters_k, "total:errors", 1)
            pipe.hincrby(counters_k, f"errkind:{domain.failure_kind(exc)}", 1)
            pipe.expire(counters_k, params.COUNTER_TTL_S)
            await pipe.execute()
    except Exception as e:
        logger.debug(f"[llm-counter] failure bump failed: {e}")


# ---------------------------------------------------------------------------
# Reading + snapshot
# ---------------------------------------------------------------------------

async def _read_usage(r: Any, ns: str, run_id: str) -> dict[str, Any] | None:
    """The run's `build_usage` payload, or None when Redis holds no counters for it. Raises on Redis errors."""
    counters = domain.decode_hash(await r.hgetall(keys.counters_key(ns, run_id)))
    if not counters:
        return None
    node_ids = domain.split_counters(counters)[2]
    by_model = {node_id: domain.parse_models(await r.hgetall(keys.models_key(ns, run_id, node_id))) for node_id in node_ids}
    return domain.build_usage(counters, by_model)


async def _read_snapshot(ns: str, run_id: str) -> dict[str, Any] | None:
    try:
        data = json.loads(await infra.minio.service.get_storage().read_text(keys.snapshot_key(ns, run_id)))
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


async def read_counters(ns: str, run_id: str) -> dict[str, Any]:
    """The usage payload of one run, in its namespace's shape — what the Pipeline / Ask usage drawer renders. Redis first;
    a namespace with a snapshot (DD) falls back to it once Redis has expired the run."""
    spec = params.NAMESPACES.get(ns, params.DEFAULT_NAMESPACE)
    empty = domain.empty_payload(spec, run_id)
    if not run_id:
        return empty

    async def _fallback() -> dict[str, Any]:
        return (await _read_snapshot(ns, run_id) if spec.snapshot else None) or empty

    try:
        async with infra.redis.service.client() as r:
            usage = await _read_usage(r, ns, run_id)
    except Exception as e:
        logger.warning(f"[llm-counter] read failed ns={ns} run={run_id}: {e}")
        return await _fallback()
    if usage is None:
        return await _fallback()
    return domain.payload(spec, run_id, usage)


async def snapshot(ns: str, run_id: str) -> bool:
    """Persist a finished run's Redis counters to MinIO (namespaces with a snapshot only)."""
    if not params.NAMESPACES.get(ns, params.DEFAULT_NAMESPACE).snapshot:
        return False
    try:
        payload = await read_counters(ns, run_id)
    except Exception as e:
        logger.warning(f"[llm-counter] snapshot read failed ns={ns} run={run_id}: {type(e).__name__}: {e}")
        return False
    total = (payload or {}).get("total") or {}
    calls = int(total.get("calls") or 0)
    if calls <= 0:
        logger.info(f"[llm-counter] snapshot skipped ns={ns} run={run_id} (zero calls)")
        return False
    try:
        await infra.minio.service.get_storage().write(
            keys.snapshot_key(ns, run_id),
            json.dumps(payload, ensure_ascii = False, indent = 2),
            content_type = "application/json",
        )
        by_node_errors = {n: v.get("errors", 0) for n, v in ((payload or {}).get("by_node") or {}).items() if v.get("errors")}
        logger.info(
            f"[llm-counter] snapshot persisted ns={ns} run={run_id} calls={calls} errors={int(total.get('errors') or 0)} "
            f"by_node_errors={by_node_errors} kinds={(payload or {}).get('error_kinds') or {}}"
        )
        return True
    except Exception as e:
        logger.warning(f"[llm-counter] snapshot write failed ns={ns} run={run_id}: {type(e).__name__}: {e}")
        return False


# ---------------------------------------------------------------------------
# LangChain callback
# ---------------------------------------------------------------------------

class LLMCounterCallback(AsyncCallbackHandler):
    """Counts every LLM call made through the LLM it is attached to (`llm.with_config(callbacks=[LLMCounterCallback()])`)
    on the current attribution, without needing the call's return value — `LLMGraphTransformer` never exposes it.
    `with_structured_output` goes through the same hooks (it only wraps the inner Runnable). A fresh instance per
    `.with_config(...)`; `_pending` holds the span / timer of each in-flight call, keyed by LangChain's `run_id`."""

    def __init__(self) -> None:
        super().__init__()
        self._pending: dict[Any, tuple[object | None, float, str]] = {}

    async def on_llm_start(self, serialized: dict, prompts: list, *, run_id: Any, **kwargs: Any) -> None:
        """Opens the span / timer of this call, closed in `on_llm_end` or `on_llm_error`. The request model is read off
        the endpoint singleton, as the chat adapter does before its own response arrives."""
        try:
            model = domains.settings.chat.service.ENDPOINT.model
            span = domains.settings.observability.service.start_chat_span(model = model)
            self._pending[run_id] = (span, time.monotonic(), model)
        except Exception as e:
            logger.warning(f"[llm-counter] on_llm_start failed: {type(e).__name__}: {e}")

    async def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        span, t0, req_model = self._pending.pop(run_id, (None, None, None))
        try:
            usage = domain.extract_usage(response)
            model = usage.model or "unknown"
            await bump_current_call(usage = usage, model = model)
            domains.settings.observability.service.record_chat_response(
                span, model = model, input_tokens = usage.tokens_in or None, output_tokens = usage.tokens_out or None,
            )
            domains.settings.observability.service.record_gen_ai_call(
                operation     = "chat",
                model         = model if usage.model else (req_model or "unknown"),
                outcome       = "ok",
                duration_s    = (time.monotonic() - t0) if t0 is not None else 0.0,
                input_tokens  = usage.tokens_in or None,
                output_tokens = usage.tokens_out or None,
            )
        except Exception as e:
            logger.warning(f"[llm-counter] on_llm_end failed: {type(e).__name__}: {e}")
        finally:
            domains.settings.observability.service.end_chat_span(span)

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        """LangChain fires this instead of `on_llm_end` when the call raises — e.g. the `InternalServerError: 504`
        timeouts that halt a run's retry passes, otherwise visible only in Loki."""
        span, t0, req_model = self._pending.pop(run_id, (None, None, None))
        try:
            outcome = "timeout" if "timeout" in (type(error).__name__ + str(error)).lower() else "error"
            domains.settings.observability.service.record_gen_ai_call(
                operation  = "chat",
                model      = req_model or "unknown",
                outcome    = outcome,
                duration_s = (time.monotonic() - t0) if t0 is not None else 0.0,
            )
        except Exception as e:
            logger.warning(f"[llm-counter] on_llm_error failed: {type(e).__name__}: {e}")
        finally:
            domains.settings.observability.service.end_chat_span(span, error = error)
