"""Per-scan LLM call + token counters aggregated in Redis; read by FastAPI drawer."""
from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from .keys import redis_url
from .params import (
    REDIS_CONNECT_TIMEOUT_S,
    REDIS_OP_TIMEOUT_S,
    SNAPSHOT_TTL_S,
)


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


_LLM_COUNTERS_TTL_S: int = SNAPSHOT_TTL_S
_KNOWN_PHASES: tuple[str, ...] = (
    "orchestrator",
    "discovery",
    "triage",
    "deep_read",
    "graph_build",
    "synthesis",
)

# All 4 discovery subagents share the "discovery" bucket — they fan out in parallel and belong to one pipeline node.
_SUBAGENT_TYPE_TO_PHASE: dict[str, str] = {
    "discovery_arxiv":                       "discovery",
    "discovery_semantic_scholar":            "discovery",
    "discovery_huggingface_daily_papers":    "discovery",
    "discovery_hn":                          "discovery",
    "deep_read":                             "deep_read",
    "synthesis":                             "synthesis",
}


def _phase_field_prefix(phase: str) -> str:
    return f"phase:{phase}"


def _counters_key(scan_id: str) -> str:
    return f"rr:{scan_id}:llm:counters"


def _models_key(scan_id: str, phase: str) -> str:
    return f"rr:{scan_id}:llm:models:{phase}"


class RRLlmCounterCallback(BaseCallbackHandler):
    """LangChain callback that bumps Redis counters per LLM completion.

    Skips silently when no scan_id is in the context (non-RR callers).
    """

    raise_error = False
    run_inline  = True

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str] | None,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        try:
            self._phase_from_tags(tags or [])
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_llm_start failed: {e}")

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: Any,
        *,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        # ChatModels emit on_chat_model_start instead of on_llm_start.
        try:
            self._phase_from_tags(tags or [])
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_chat_model_start failed: {e}")

    def on_llm_end(self, response: Any, **_: Any) -> None:
        """Bump counters on every successful completion.

        Uses group-name fallback (`rr-strong`) for model id — registering a LiteLLM
        success_callback broke msgpack serialization in langgraph's InMemorySaver.put_writes.
        """
        try:
            scan_id = get_scan()
            if not scan_id:
                return
            phase = get_phase()
            model, tokens_in, tokens_out = _extract_usage(response)
            _bump_sync(
                scan_id   = scan_id,
                phase     = phase,
                model     = model or "unknown",
                tokens_in = tokens_in,
                tokens_out= tokens_out,
            )
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_llm_end bump failed: {e}")

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
            phase = _SUBAGENT_TYPE_TO_PHASE.get(subagent_type)
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
            import json
            try:
                parsed = json.loads(input_str)
                if isinstance(parsed, dict):
                    val = parsed.get("subagent_type")
                    if isinstance(val, str) and val:
                        return val
            except (json.JSONDecodeError, ValueError):
                import re
                m = re.search(
                    r"subagent_type['\"]?\s*[:=]\s*['\"]([\w_]+)['\"]",
                    input_str,
                )
                if m:
                    return m.group(1)
        return None


# Group names that are rotator pool aliases, not real deployments.
_ROTATOR_GROUP_NAMES: frozenset[str] = frozenset({
    "rr-strong", "dd-all", "dd-synth", "dd-reduce-label",
    "dd-keylm", "dd-embed",
})


def _pick_first_real_model(*candidates: Any) -> str | None:
    """Return the first non-empty model string that isn't a rotator group name."""
    for c in candidates:
        if isinstance(c, str) and c and c not in _ROTATOR_GROUP_NAMES:
            return c
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


def _extract_usage(response: Any) -> tuple[str | None, int, int]:
    """Return (model_id, tokens_in, tokens_out) from a LangChain LLMResult."""
    model_candidates: list[Any] = []
    tokens_in = 0
    tokens_out = 0

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        model_candidates += [
            llm_output.get("model_name"),
            llm_output.get("model"),
            llm_output.get("model_id"),
        ]
        token_usage = llm_output.get("token_usage") or {}
        if isinstance(token_usage, dict):
            tokens_in  = int(token_usage.get("prompt_tokens")     or tokens_in)
            tokens_out = int(token_usage.get("completion_tokens") or tokens_out)

    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        gen = generations[0][0]
        message = getattr(gen, "message", None)
        if message is not None:
            um = getattr(message, "usage_metadata", None)
            if isinstance(um, dict):
                tokens_in  = int(um.get("input_tokens")  or tokens_in)
                tokens_out = int(um.get("output_tokens") or tokens_out)
            rm = getattr(message, "response_metadata", None) or {}
            if isinstance(rm, dict):
                model_candidates += [
                    rm.get("model_name"),
                    rm.get("model"),
                    rm.get("ls_model_name"),
                    rm.get("ls_provider"),
                ]
                raw = rm.get("response") or rm.get("model_extra") or {}
                if isinstance(raw, dict):
                    model_candidates += [raw.get("model"), raw.get("model_name")]
            ak = getattr(message, "additional_kwargs", None)
            if isinstance(ak, dict):
                model_candidates += [ak.get("model"), ak.get("model_name")]

    model_id = _pick_first_real_model(*model_candidates)
    return model_id, max(0, tokens_in), max(0, tokens_out)


def bump_retry_sync(scan_id: str, phase: str) -> None:
    """Sync retry counter bump — increments phase:X:retries + total:retries in the per-scan HASH."""
    if not scan_id or not phase:
        return
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-retry] connect failed: {e}")
        return
    try:
        counters_k = _counters_key(scan_id)
        pp         = _phase_field_prefix(phase)
        pipe       = r.pipeline(transaction=False)
        pipe.hincrby(counters_k, f"{pp}:retries", 1)
        pipe.hincrby(counters_k, "total:retries",  1)
        pipe.expire(counters_k, _LLM_COUNTERS_TTL_S)
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
    import redis as redis_sync
    try:
        r = redis_sync.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-llm-counter] connect failed: {e}")
        return

    try:
        counters_k = _counters_key(scan_id)
        models_k   = _models_key(scan_id, phase)
        pp = _phase_field_prefix(phase)

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
        pipe.expire(counters_k, _LLM_COUNTERS_TTL_S)
        pipe.expire(models_k,   _LLM_COUNTERS_TTL_S)
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

    import redis.asyncio as redis_aio
    try:
        r = redis_aio.from_url(
            redis_url(),
            socket_connect_timeout = REDIS_CONNECT_TIMEOUT_S,
            socket_timeout         = REDIS_OP_TIMEOUT_S,
        )
    except Exception as e:
        logger.warning(f"[rr-llm-counter] connect failed: {e}")
        return empty

    try:
        counters_raw = await r.hgetall(_counters_key(scan_id))
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
            models_raw = await r.hgetall(_models_key(scan_id, phase))
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
        from uuid import UUID
        from ..stores.postgres import read_llm_counters
        return await read_llm_counters(UUID(scan_id))
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
        from uuid import UUID
        from ..stores.postgres import write_llm_counters
        ok = await write_llm_counters(UUID(scan_id), payload)
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


def _litellm_pull_model_id(kwargs: dict | None, response: Any) -> str:
    """Best deployment id from LiteLLM success_callback args; falls back to 'unknown'."""
    model = getattr(response, "model", None)
    if isinstance(model, str) and model:
        return model
    if isinstance(response, dict):
        v = response.get("model")
        if isinstance(v, str) and v:
            return v
    hp = getattr(response, "_hidden_params", None)
    if isinstance(hp, dict):
        v = hp.get("model_id") or hp.get("model")
        if isinstance(v, str) and v:
            return v
    if isinstance(kwargs, dict):
        v = kwargs.get("model")
        if isinstance(v, str) and v:
            return v
    return "unknown"


def _litellm_pull_usage(response: Any) -> tuple[int, int]:
    """Pull prompt/completion tokens from a LiteLLM ModelResponse."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    if hasattr(usage, "prompt_tokens") or hasattr(usage, "completion_tokens"):
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
        )
    if isinstance(usage, dict):
        return (
            int(usage.get("prompt_tokens",     0) or 0),
            int(usage.get("completion_tokens", 0) or 0),
        )
    return 0, 0


def _litellm_success_callback(
    kwargs: dict | None,
    response: Any,
    start_time: Any = None,
    end_time:   Any = None,
) -> None:
    """LiteLLM success callback — bumps Redis counters with the real deployment name."""
    try:
        scan_id = get_scan()
        if not scan_id:
            return
        phase = get_phase()
        model = _litellm_pull_model_id(kwargs, response)
        tokens_in, tokens_out = _litellm_pull_usage(response)
        _bump_sync(
            scan_id    = scan_id,
            phase      = phase,
            model      = model,
            tokens_in  = tokens_in,
            tokens_out = tokens_out,
        )
    except Exception as e:
        logger.warning(f"[rr-llm-counter] litellm success callback bump failed: {e}")


def _register_with_litellm() -> None:
    """DISABLED — registering this callback caused litellm to attach a function reference to
    response._hidden_params, which propagated into AIMessage.response_metadata and broke
    langgraph's InMemorySaver.put_writes with msgpack serialization errors."""
    return


# Intentionally NOT invoked. Re-enable only after a msgpack-safe deployment extraction path is wired.
# _register_with_litellm()


__all__ = [
    "RRLlmCounterCallback",
    "set_scan",
    "get_scan",
    "set_phase",
    "get_phase",
    "read_counters",
]
