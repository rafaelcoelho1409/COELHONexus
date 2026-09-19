"""Per-DD-thread LLM call/token counters; Redis for in-flight, MinIO snapshot on completion — avoids live Langfuse queries for the drawer UI."""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import Any

import domains

from . import domain, keys, params


logger = logging.getLogger(__name__)


_stage_var: ContextVar[str | None] = ContextVar(
    "dd_llm_stage", default=None,
)
_thread_id_var: ContextVar[str | None] = ContextVar(
    "dd_llm_thread_id", default=None,
)
_node_id_var: ContextVar[str | None] = ContextVar(
    "dd_llm_node_id", default=None,
)


def set_context(
    *,
    stage: str | None,
    thread_id: str | None,
    node_id: str | None,
) -> None:
    """Set the current DD LLM attribution context for this async task."""
    _stage_var.set(stage)
    _thread_id_var.set(thread_id)
    _node_id_var.set(node_id)


def clear_context() -> None:
    set_context(stage=None, thread_id=None, node_id=None)


def get_context() -> tuple[str | None, str | None, str | None]:
    return _stage_var.get(), _thread_id_var.get(), _node_id_var.get()


def bump_current_call(
    *,
    response: Any,
    deployment: str | None = None,
) -> dict[str, Any] | None:
    """Bump counters for the current DD context; no-op when called outside a DD context (other domains)."""
    stage, thread_id, node_id = get_context()
    if not stage or not thread_id or not node_id:
        return None
    tokens_in, tokens_out, reasoning_tokens = domain.extract_usage(response)
    model = domain._model_from_response(response, deployment)
    _bump_sync(
        stage=stage,
        thread_id=thread_id,
        node_id=node_id,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        reasoning_tokens=reasoning_tokens,
    )
    return {
        "stage": stage,
        "thread_id": thread_id,
        "node_id": node_id,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "reasoning_tokens": reasoning_tokens,
    }


def _bump_sync(
    *,
    stage: str,
    thread_id: str,
    node_id: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int,
) -> None:
    import redis as redis_sync

    try:
        r = redis_sync.from_url(
            domains.dd.planner.keys.redis_url(),
            socket_connect_timeout=domains.dd.planner.params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout=domains.dd.planner.params.REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[dd-llm-counter] connect failed: {e}")
        return

    try:
        counters_k = keys.counters_key(thread_id)
        models_k = keys.models_key(thread_id, node_id)
        pp = f"node:{node_id}"
        pipe = r.pipeline(transaction=False)

        pipe.hset(counters_k, "stage", stage)
        pipe.hincrby(counters_k, f"{pp}:calls", 1)
        pipe.hincrby(counters_k, f"{pp}:tokens_in", int(tokens_in))
        pipe.hincrby(counters_k, f"{pp}:tokens_out", int(tokens_out))
        pipe.hincrby(
            counters_k,
            f"{pp}:reasoning_tokens",
            int(reasoning_tokens),
        )
        pipe.hincrby(counters_k, "total:calls", 1)
        pipe.hincrby(counters_k, "total:tokens_in", int(tokens_in))
        pipe.hincrby(counters_k, "total:tokens_out", int(tokens_out))
        pipe.hincrby(
            counters_k,
            "total:reasoning_tokens",
            int(reasoning_tokens),
        )

        pipe.hincrby(models_k, f"{model}:calls", 1)
        pipe.hincrby(models_k, f"{model}:tokens_in", int(tokens_in))
        pipe.hincrby(models_k, f"{model}:tokens_out", int(tokens_out))
        pipe.hincrby(
            models_k,
            f"{model}:reasoning_tokens",
            int(reasoning_tokens),
        )
        pipe.expire(counters_k, params.COUNTER_TTL_S)
        pipe.expire(models_k, params.COUNTER_TTL_S)
        pipe.execute()
    except Exception as e:
        logger.warning(
            f"[dd-llm-counter] bump failed thread_id={thread_id} "
            f"node={node_id} model={model}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            r.close()
        except Exception:
            pass


async def read_counters(thread_id: str) -> dict[str, Any]:
    empty = {
        "thread_id": thread_id,
        "stage": None,
        "total": {
            "calls": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "reasoning_tokens": 0,
        },
        "by_node": {},
    }
    if not thread_id:
        return empty

    import redis.asyncio as redis_aio

    try:
        r = redis_aio.from_url(
            domains.dd.planner.keys.redis_url(),
            socket_connect_timeout=domains.dd.planner.params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout=domains.dd.planner.params.REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[dd-llm-counter] connect failed: {e}")
        return await _read_snapshot(thread_id) or empty

    try:
        raw = await r.hgetall(keys.counters_key(thread_id))
        if not raw:
            return await _read_snapshot(thread_id) or empty
        counters = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        out = {
            "thread_id": thread_id,
            "stage": counters.get("stage"),
            "total": {
                "calls": int(counters.get("total:calls", 0) or 0),
                "tokens_in": int(counters.get("total:tokens_in", 0) or 0),
                "tokens_out": int(counters.get("total:tokens_out", 0) or 0),
                "reasoning_tokens": int(
                    counters.get("total:reasoning_tokens", 0) or 0,
                ),
            },
            "by_node": {},
        }
        node_fields: dict[str, dict[str, int]] = {}
        for field, val in counters.items():
            if not field.startswith("node:"):
                continue
            try:
                _, node_id, key = field.split(":", 2)
            except ValueError:
                continue
            node_fields.setdefault(node_id, {})[key] = int(val or 0)

        for node_id, fields in node_fields.items():
            by_model = await _read_models(r, thread_id, node_id)
            out["by_node"][node_id] = {
                "calls": int(fields.get("calls", 0)),
                "tokens_in": int(fields.get("tokens_in", 0)),
                "tokens_out": int(fields.get("tokens_out", 0)),
                "reasoning_tokens": int(fields.get("reasoning_tokens", 0)),
                "by_model": by_model,
            }
        return out
    except Exception as e:
        logger.warning(
            f"[dd-llm-counter] read failed thread_id={thread_id}: {e}"
        )
        return await _read_snapshot(thread_id) or empty
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def _read_models(r: Any, thread_id: str, node_id: str) -> dict:
    raw = await r.hgetall(keys.models_key(thread_id, node_id))
    by_model: dict[str, dict[str, int]] = {}
    for k, v in (raw or {}).items():
        key = k.decode() if isinstance(k, bytes) else k
        try:
            model, field = key.rsplit(":", 1)
        except ValueError:
            continue
        by_model.setdefault(model, {})[field] = int(v or 0)
    return by_model


async def _read_snapshot(thread_id: str) -> dict[str, Any] | None:
    try:
        raw = await domains.dd.ingestion.storage.service.get_storage().read_text(
            keys.snapshot_key(thread_id),
        )
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        return None
    return None


async def snapshot(thread_id: str) -> bool:
    """Persist current Redis counters to MinIO for completed DD runs."""
    try:
        payload = await read_counters(thread_id)
    except Exception as e:
        logger.warning(
            f"[dd-llm-counter] snapshot read failed thread_id={thread_id}: "
            f"{type(e).__name__}: {e}"
        )
        return False
    calls = int(((payload or {}).get("total") or {}).get("calls") or 0)
    if calls <= 0:
        logger.info(
            f"[dd-llm-counter] snapshot skipped thread_id={thread_id} "
            "(zero calls)"
        )
        return False
    try:
        await domains.dd.ingestion.storage.service.get_storage().write(
            keys.snapshot_key(thread_id),
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        logger.info(
            f"[dd-llm-counter] snapshot persisted thread_id={thread_id} "
            f"calls={calls}"
        )
        return True
    except Exception as e:
        logger.warning(
            f"[dd-llm-counter] snapshot write failed thread_id={thread_id}: "
            f"{type(e).__name__}: {e}"
        )
        return False
