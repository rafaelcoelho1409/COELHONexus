"""Per-scan LLM call + token counters aggregated in Redis; read by FastAPI
drawer — Imperative Shell."""
from __future__ import annotations
from . import domain, keys, params
from .. import keys as runtime_keys
from .. import params as runtime_params
from ... import stores

import json
import logging
import re
import time
from contextvars import ContextVar
from typing import Any
from uuid import UUID

import redis as redis_sync
import redis.asyncio as redis_aio

from langchain_core.callbacks import BaseCallbackHandler


logger = logging.getLogger(__name__)


_scan_id_var: ContextVar[str | None] = ContextVar("rr_llm_scan_id", default=None)
_phase_var:   ContextVar[str]        = ContextVar("rr_llm_phase",   default="orchestrator")


def set_scan(scan_id: str | None) -> None:
    _scan_id_var.set(scan_id)


def get_scan() -> str | None:
    return _scan_id_var.get()


def set_phase(phase: str) -> None:
    _phase_var.set(phase or "orchestrator")


def get_phase() -> str:
    return _phase_var.get() or "orchestrator"


class RRLlmCounterCallback(BaseCallbackHandler):
    """LangChain callback that bumps Redis counters per LLM completion.

    Skips silently when no scan_id is in the context (non-RR callers).
    """

    raise_error = False
    run_inline  = True

    def __init__(self) -> None:
        super().__init__()
        # 2026-09-17: per-call wall-time logging — root-causing a slow
        # scan previously required pulling raw span data out of
        # LangFuse by hand (confirmed live: a 522s "gap" between
        # graph_build and digest assembly turned out to be two
        # sequential ~250s rotator calls; a 945s synthesis phase was 7
        # sequential calls, one alone 364s). None of that was visible
        # from `kubectl logs`. Keyed by `run_id` (unique per LLM
        # invocation, assigned by LangChain) rather than any shared
        # state, so concurrent calls — deep_read's parallel subagent
        # fan-out included — never cross-contaminate each other's
        # timers. Entries are popped in `on_llm_end`/`on_llm_error`;
        # a run whose end callback never fires (e.g. a hard-killed
        # task) leaks one small dict entry — acceptable, matches this
        # module's existing best-effort style elsewhere.
        self._call_start: dict[Any, float] = {}

    def _mark_start(self, run_id: Any) -> None:
        if run_id is not None:
            self._call_start[run_id] = time.monotonic()

    def _pop_duration(self, run_id: Any) -> float | None:
        if run_id is None:
            return None
        t0 = self._call_start.pop(run_id, None)
        return None if t0 is None else time.monotonic() - t0

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str] | None,
        *,
        run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        self._mark_start(run_id)
        try:
            self._phase_from_tags(tags or [])
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_llm_start failed: {e}")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: Any,
        *,
        run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        # ChatModels emit on_chat_model_start instead of on_llm_start.
        self._mark_start(run_id)
        try:
            self._phase_from_tags(tags or [])
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_chat_model_start failed: {e}")

    def on_llm_end(self, response: Any, *, run_id: Any = None, **_: Any) -> None:
        """Bump counters on every successful completion.

        Uses group-name fallback (`rr-strong`) for model id — registering a LiteLLM
        success_callback broke msgpack serialization in langgraph's InMemorySaver.put_writes.
        """
        duration_s = self._pop_duration(run_id)
        try:
            scan_id = get_scan()
            if not scan_id:
                return
            phase = get_phase()
            model, tokens_in, tokens_out = domain.extract_usage(response)
            logger.info(
                f"[rr-llm-timing] scan_id={scan_id} phase={phase} "
                f"model={model or 'unknown'} "
                f"duration_s={'?' if duration_s is None else f'{duration_s:.1f}'} "
                f"tokens_in={tokens_in} tokens_out={tokens_out}"
            )
            _bump_sync(
                scan_id   = scan_id,
                phase     = phase,
                model     = model or "unknown",
                tokens_in = tokens_in,
                tokens_out= tokens_out,
            )
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_llm_end bump failed: {e}")

    def on_llm_error(self, error: BaseException, *, run_id: Any = None, **_: Any) -> None:
        """Log failed-call duration too — a slow-then-failing call is exactly
        as diagnosable-worthy as a slow-but-successful one."""
        duration_s = self._pop_duration(run_id)
        try:
            scan_id = get_scan()
            if not scan_id:
                return
            logger.info(
                f"[rr-llm-timing] scan_id={scan_id} phase={get_phase()} "
                f"FAILED after "
                f"duration_s={'?' if duration_s is None else f'{duration_s:.1f}'} "
                f"error={type(error).__name__}: {str(error)[:200]}"
            )
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_llm_error logging failed: {e}")

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        inputs: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        """Catch the DeepAgents task(subagent_type=X) dispatch before the subagent's asyncio task forks.

        Updating _phase_var here means the forked subagent inherits the new phase via asyncio's
        copy_context() — every LLM call inside the subagent attributes to its phase bucket.
        """
        try:
            if not get_scan():
                return
            tool_name = (serialized or {}).get("name") or ""
            if tool_name.lower() != "task":
                return
            subagent_type = self._extract_subagent_type(inputs, input_str)
            if not subagent_type:
                return
            phase = params.SUBAGENT_TYPE_TO_PHASE.get(subagent_type)
            if phase:
                set_phase(phase)
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_tool_start failed: {e}")

    def on_tool_end(
        self,
        output: Any,
        **kwargs: Any,
    ) -> None:
        """Restore orchestrator phase after a task() tool returns so subsequent turns don't bleed into the subagent's bucket."""
        try:
            if not get_scan():
                return
            set_phase("orchestrator")
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_tool_end failed: {e}")

    @staticmethod
    def _phase_from_tags(tags: list[str]) -> None:
        for tag in reversed(tags):
            if isinstance(tag, str) and tag.startswith("rr:phase:"):
                set_phase(tag[len("rr:phase:"):] or "orchestrator")
                return

    @staticmethod
    def _extract_subagent_type(
        inputs: dict[str, Any] | None,
        input_str: str | None,
    ) -> str | None:
        if isinstance(inputs, dict):
            val = inputs.get("subagent_type")
            if isinstance(val, str) and val:
                return val
        if input_str and isinstance(input_str, str):
            try:
                parsed = json.loads(input_str)
                if isinstance(parsed, dict):
                    val = parsed.get("subagent_type")
                    if isinstance(val, str) and val:
                        return val
            except (json.JSONDecodeError, ValueError):
                m = re.search(
                    r"subagent_type['\"]?\s*[:=]\s*['\"]([\w_]+)['\"]",
                    input_str,
                )
                if m:
                    return m.group(1)
        return None


def bump_retry_sync(scan_id: str, phase: str) -> None:
    """Sync retry counter bump — increments phase:X:retries + total:retries in the per-scan HASH."""
    if not scan_id or not phase:
        return
    try:
        r = redis_sync.from_url(
            runtime_keys.redis_url(),
            socket_connect_timeout = runtime_params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = runtime_params.REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-retry] connect failed: {e}")
        return
    try:
        counters_k = keys.counters_key(scan_id)
        pp         = keys.phase_field_prefix(phase)
        pipe       = r.pipeline(transaction=False)
        pipe.hincrby(counters_k, f"{pp}:retries", 1)
        pipe.hincrby(counters_k, "total:retries",  1)
        pipe.expire(counters_k, params.LLM_COUNTERS_TTL_S)
        pipe.execute()
        logger.info(
            f"[rr-retry] bumped phase={phase!r} scan_id={scan_id}"
        )
    except Exception as e:
        logger.warning(
            f"[rr-retry] bump failed scan_id={scan_id} phase={phase}: "
            f"{type(e).__name__}: {e}"
        )
    finally:
        try:
            r.close()
        except Exception:
            pass


def _bump_sync(
    *,
    scan_id:   str,
    phase:     str,
    model:     str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Sync Redis pipeline bumping totals + per-model breakdown. Best-effort."""
    try:
        r = redis_sync.from_url(
            runtime_keys.redis_url(),
            socket_connect_timeout = runtime_params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = runtime_params.REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-llm-counter] connect failed: {e}")
        return

    try:
        counters_k = keys.counters_key(scan_id)
        models_k   = keys.models_key(scan_id, phase)
        pp = keys.phase_field_prefix(phase)

        pipe = r.pipeline(transaction=False)
        pipe.hincrby(counters_k, f"{pp}:calls",      1)
        pipe.hincrby(counters_k, f"{pp}:tokens_in",  int(tokens_in))
        pipe.hincrby(counters_k, f"{pp}:tokens_out", int(tokens_out))
        # Scan-wide totals (denormalized so the reader doesn't sum over phases)
        pipe.hincrby(counters_k, "total:calls",      1)
        pipe.hincrby(counters_k, "total:tokens_in",  int(tokens_in))
        pipe.hincrby(counters_k, "total:tokens_out", int(tokens_out))
        pipe.hincrby(models_k, f"{model}:calls",      1)
        pipe.hincrby(models_k, f"{model}:tokens_in",  int(tokens_in))
        pipe.hincrby(models_k, f"{model}:tokens_out", int(tokens_out))
        pipe.expire(counters_k, params.LLM_COUNTERS_TTL_S)
        pipe.expire(models_k,   params.LLM_COUNTERS_TTL_S)
        pipe.execute()
    except Exception as e:
        logger.warning(
            f"[rr-llm-counter] bump failed scan_id={scan_id} phase={phase} "
            f"model={model}: {type(e).__name__}: {e}"
        )
    finally:
        try:
            r.close()
        except Exception:
            pass


async def read_counters(scan_id: str) -> dict[str, Any]:
    """Read all counters for a scan as a structured dict with total + by_phase breakdown."""
    empty: dict[str, Any] = {
        "scan_id":  scan_id,
        "total":    {"calls": 0, "tokens_in": 0, "tokens_out": 0},
        "by_phase": {},
    }
    if not scan_id:
        return empty

    try:
        r = redis_aio.from_url(
            runtime_keys.redis_url(),
            socket_connect_timeout = runtime_params.REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = runtime_params.REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-llm-counter] connect failed: {e}")
        return empty

    try:
        counters_raw = await r.hgetall(keys.counters_key(scan_id))
        if not counters_raw:
            return await _read_from_postgres(scan_id) or empty

        counters = {
            (k.decode() if isinstance(k, bytes) else k):
            (v.decode() if isinstance(v, bytes) else v)
            for k, v in counters_raw.items()
        }

        out: dict[str, Any] = {
            "scan_id":  scan_id,
            "total":    {
                "calls":      int(counters.get("total:calls",      0) or 0),
                "tokens_in":  int(counters.get("total:tokens_in",  0) or 0),
                "tokens_out": int(counters.get("total:tokens_out", 0) or 0),
                "retries":    int(counters.get("total:retries",    0) or 0),
            },
            "by_phase": {},
        }
        phase_fields: dict[str, dict[str, int]] = {}
        for field, val in counters.items():
            if not field.startswith("phase:"):
                continue
            try:
                _, phase, key = field.split(":", 2)
            except ValueError:
                continue
            phase_fields.setdefault(phase, {})[key] = int(val or 0)

        for phase, fields in phase_fields.items():
            models_raw = await r.hgetall(keys.models_key(scan_id, phase))
            by_model: dict[str, dict[str, int]] = {}
            for k, v in (models_raw or {}).items():
                ks = k.decode() if isinstance(k, bytes) else k
                try:
                    model, mfield = ks.rsplit(":", 1)
                except ValueError:
                    continue
                by_model.setdefault(model, {})[mfield] = int(v or 0)
            out["by_phase"][phase] = {
                "calls":      int(fields.get("calls",      0)),
                "tokens_in":  int(fields.get("tokens_in",  0)),
                "tokens_out": int(fields.get("tokens_out", 0)),
                "retries":    int(fields.get("retries",    0)),
                "by_model":   by_model,
            }
        return out
    except Exception as e:
        logger.warning(f"[rr-llm-counter] read failed scan_id={scan_id}: {e}")
        return empty
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


async def _read_from_postgres(scan_id: str) -> dict[str, Any] | None:
    """Fallback read from radar_scans.llm_counters when Redis TTL'd. Never raises."""
    try:
        return await stores.service.read_llm_counters(UUID(scan_id))
    except Exception as e:
        logger.warning(
            f"[rr-llm-counter] postgres fallback read failed "
            f"scan_id={scan_id}: {type(e).__name__}: {e}"
        )
        return None


async def snapshot_to_postgres(scan_id: str) -> bool:
    """Persist counter state from Redis to Postgres at scan end; skips when total.calls == 0."""
    try:
        payload = await read_counters(scan_id)
    except Exception as e:
        logger.warning(
            f"[rr-llm-counter] snapshot read_counters failed "
            f"scan_id={scan_id}: {type(e).__name__}: {e}"
        )
        return False
    total = (payload or {}).get("total") or {}
    if not int(total.get("calls") or 0):
        logger.info(
            f"[rr-llm-counter] snapshot skipped scan_id={scan_id} "
            f"(zero calls — nothing to persist)"
        )
        return False
    try:
        ok = await stores.service.write_llm_counters(UUID(scan_id), payload)
        if ok:
            logger.info(
                f"[rr-llm-counter] snapshot persisted scan_id={scan_id} "
                f"calls={total.get('calls')} → radar_scans.llm_counters"
            )
        else:
            logger.warning(
                f"[rr-llm-counter] snapshot UPDATE matched 0 rows "
                f"scan_id={scan_id} — scan row may have been deleted"
            )
        return ok
    except Exception as e:
        logger.warning(
            f"[rr-llm-counter] snapshot write failed "
            f"scan_id={scan_id}: {type(e).__name__}: {e}"
        )
        return False


def bump_llm_usage(
    *,
    scan_id:    str,
    phase:      str,
    model:      str,
    tokens_in:  int,
    tokens_out: int,
) -> None:
    """Public wrapper over `_bump_sync` for callers outside the LangChain
    callback path (`runtime/service.py`'s `capture_llm_usage`) — one-off
    `chain.ainvoke()` calls in `task.py`'s backfill and `agent/tools/code_synth/service.py`
    that run outside `agent.ainvoke()`'s `config={"callbacks":[...]}`, so
    `RRLlmCounterCallback.on_llm_end` never fires for them."""
    _bump_sync(
        scan_id    = scan_id,
        phase      = phase,
        model      = model,
        tokens_in  = tokens_in,
        tokens_out = tokens_out,
    )
