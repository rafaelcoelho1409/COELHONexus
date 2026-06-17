"""Per-scan LLM call + token counters (Path A, 2026-06-16).

Aggregates LLM activity for an RR scan into Redis so the FastHTML drawer
can render KPI cards per pipeline node when the user clicks it.

Pattern mirrors fs_mirror.py: sync writes from inside the langchain
callback (it runs synchronously on the Celery loop thread); async reads
from FastAPI. TTL matches the snapshot TTL so a finished scan's counters
are still queryable in the drawer until cleanup.

What's tracked, per (scan_id, phase):
  - llm_calls      ↑1 per successful `on_llm_end`
  - tokens_in      ↑ prompt_tokens   (from `usage_metadata.input_tokens`)
  - tokens_out     ↑ completion_tokens (from `usage_metadata.output_tokens`)
  - by_model       Redis HASH model_id → "calls,tokens_in,tokens_out"

Phase attribution (2026-06-16 v2 — on_tool_start hook):
  - The DeepAgents `task(subagent_type=X, ...)` tool is the dispatch
    boundary. When orchestrator emits a `task()` tool_call, langchain
    fires `on_tool_start` in the orchestrator's task BEFORE the tool
    body runs (the tool body spawns the subagent's asyncio task).
  - The callback intercepts on_tool_start, parses `subagent_type` from
    `inputs`, and updates the orchestrator's `_phase_var`. Because
    asyncio's `copy_context()` snapshots the parent's contextvars at
    fork time, the subagent task INHERITS the new phase — every LLM
    call inside the subagent attributes to its phase bucket.
  - `on_tool_end` restores phase to "orchestrator" so the next
    orchestrator turn (between subagent dispatches) attributes back.
  - Fallback: deterministic tools (`triage_candidates`, `graph_build_papers`,
    `write_synthesis_report`) also set_phase explicitly at the end of
    their bodies — when the orchestrator processes their results, the
    next planning turn attributes to that phase.
  - Calls without any phase context are attributed to "orchestrator"
    (the top-level agent's own planning turns).

Phase 2 (later, after LangFuse query layer is up): replace this with a
LangFuse-trace aggregation by `trace_id == scan_id`. The Redis path stays
as a fast in-flight cache. See docs/RR-OBSERVABILITY-PLAN-2026-06-16.md
when written.
"""
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


# --------------------------------------------------------------------------- #
# Context vars — set by task.py at scan entry; threaded by langchain into
# every nested runnable's callback context.
# --------------------------------------------------------------------------- #
_scan_id_var: ContextVar[str | None] = ContextVar("rr_llm_scan_id", default=None)
_phase_var:   ContextVar[str]        = ContextVar("rr_llm_phase",   default="orchestrator")


def set_scan(scan_id: str | None) -> None:
    """Set the current scan_id for the running task's context. Called by
    task.py at the start of each scan. None clears."""
    _scan_id_var.set(scan_id)


def get_scan() -> str | None:
    """Read the current scan_id from the context. None when no RR scan
    is in flight (callback then no-ops)."""
    return _scan_id_var.get()


def set_phase(phase: str) -> None:
    """Set the current phase. Called from the callback's on_llm_start
    when it sees an `rr:phase:<X>` tag, OR explicitly from PhaseEnforcer
    hooks if we ever need it."""
    _phase_var.set(phase or "orchestrator")


def get_phase() -> str:
    """Read the current phase (defaults to 'orchestrator')."""
    return _phase_var.get() or "orchestrator"


# --------------------------------------------------------------------------- #
# Redis key layout
# --------------------------------------------------------------------------- #
_LLM_COUNTERS_TTL_S: int = SNAPSHOT_TTL_S
_KNOWN_PHASES: tuple[str, ...] = (
    "orchestrator",
    "discovery",
    "triage",
    "deep_read",
    "graph_build",
    "synthesis",
)


# DeepAgents subagent_type → phase bucket. All 4 discovery subagents
# share the "discovery" bucket because they fan out in parallel and
# semantically belong to one pipeline node.
_SUBAGENT_TYPE_TO_PHASE: dict[str, str] = {
    "discovery_arxiv":                       "discovery",
    "discovery_semantic_scholar":            "discovery",
    "discovery_huggingface_daily_papers":    "discovery",
    "discovery_hn":                          "discovery",
    "deep_read":                             "deep_read",
    "synthesis":                             "synthesis",
}


def _phase_field_prefix(phase: str) -> str:
    """Field-name prefix inside the per-scan counters HASH."""
    return f"phase:{phase}"


def _counters_key(scan_id: str) -> str:
    """One HASH per scan holding {phase:X:calls, phase:X:tokens_in, ...}."""
    return f"rr:{scan_id}:llm:counters"


def _models_key(scan_id: str, phase: str) -> str:
    """One HASH per (scan_id, phase) holding {model: 'calls,in,out'}."""
    return f"rr:{scan_id}:llm:models:{phase}"


# --------------------------------------------------------------------------- #
# Callback handler — single instance attached to the rotator model so
# every LangChain LLM call funnels through it.
# --------------------------------------------------------------------------- #
class RRLlmCounterCallback(BaseCallbackHandler):
    """LangChain callback that bumps Redis counters per LLM completion.

    `on_llm_start`: parses incoming `tags` for `rr:phase:<X>`; updates the
        phase contextvar so `on_llm_end` knows where to attribute. Tag
        propagation happens automatically through langchain RunnableConfig
        when the subagent's model is built with `.with_config(tags=[...])`.

    `on_llm_end`: extracts usage from the response and bumps counters.
        Always best-effort; an exception here MUST NOT bubble into the
        agent run (we log and swallow). Skipped entirely when no scan_id
        is in the context (non-RR callers, e.g. DD planner).
    """

    raise_error = False
    run_inline  = True

    # Subclasses of BaseCallbackHandler can return whatever they want
    # from on_*; LangChain ignores it. We use these only for side effects.

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
        """Bump per-(scan, phase, model) Redis counters on every successful
        chat completion.

        2026-06-16 REVERT: an earlier attempt registered a LiteLLM-side
        `success_callback` to surface the real deployment name (instead
        of the rotator group `rr-strong`). That broke msgpack
        serialization in langgraph's `InMemorySaver.put_writes` — the
        AIMessage's `response_metadata` or `_hidden_params` picked up a
        function reference from the callback registry that msgpack
        couldn't pack. Scan `f8714b75` crashed with `TypeError: Type is
        not msgpack serializable: AIMessage`. Reverting to the
        langchain `on_llm_end` path here — we get the group name
        (`rr-strong`) for now; the real deployment will need a non-
        mutating extraction path."""
        try:
            scan_id = get_scan()
            if not scan_id:
                return  # not an RR call; skip
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
        """Catch the DeepAgents `task(subagent_type=X)` dispatch BEFORE
        the subagent's asyncio task is forked. Updating the orchestrator's
        `_phase_var` here means the forked subagent inherits the new
        phase via asyncio's `copy_context()` — all LLM calls inside the
        subagent then attribute to its phase bucket.

        Strategy: look at the tool name (`task` or `Task` per DeepAgents
        convention); parse `subagent_type` from `inputs` (preferred — it's
        the already-parsed args dict) or from `input_str` (fallback —
        JSON-encoded args)."""
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
        """When a `task()` tool returns, the subagent has finished and
        control is back to the orchestrator. Restore the orchestrator
        phase so subsequent orchestrator turns don't bleed into the
        previous subagent's bucket."""
        try:
            if not get_scan():
                return
            # The `serialized` dict isn't surfaced on on_tool_end in all
            # langchain versions; we play it safe by always restoring
            # to "orchestrator" on tool end. This is correct because:
            # only `task()` flips phase to a non-orchestrator value;
            # other tool ends are no-ops (their phase was already set
            # by their own bodies via fs-write side-effects).
            set_phase("orchestrator")
        except Exception as e:
            logger.warning(f"[rr-llm-counter] on_tool_end failed: {e}")

    # ----- internal ------------------------------------------------------ #

    @staticmethod
    def _phase_from_tags(tags: list[str]) -> None:
        """Pick the most recent `rr:phase:<X>` tag and set the phase var.
        Tags are propagated through nested runnables, so the innermost
        subagent's tag wins."""
        for tag in reversed(tags):
            if isinstance(tag, str) and tag.startswith("rr:phase:"):
                set_phase(tag[len("rr:phase:"):] or "orchestrator")
                return
        # No tag → don't change the current phase. Subsequent on_llm_end
        # attributes to whatever the surrounding phase already was.

    @staticmethod
    def _extract_subagent_type(
        inputs: dict[str, Any] | None,
        input_str: str | None,
    ) -> str | None:
        """Pull `subagent_type` from either the parsed `inputs` dict
        (preferred) or the JSON-encoded `input_str` (fallback). Returns
        None when the field can't be located."""
        if isinstance(inputs, dict):
            val = inputs.get("subagent_type")
            if isinstance(val, str) and val:
                return val
        if input_str and isinstance(input_str, str):
            # Cheap JSON parse — DeepAgents typically serializes the tool
            # args as a JSON object when calling the tool body.
            import json
            try:
                parsed = json.loads(input_str)
                if isinstance(parsed, dict):
                    val = parsed.get("subagent_type")
                    if isinstance(val, str) and val:
                        return val
            except (json.JSONDecodeError, ValueError):
                # Last-resort: regex for `subagent_type` if it's embedded
                # in a non-JSON form. Cheap; failing is acceptable.
                import re
                m = re.search(
                    r"subagent_type['\"]?\s*[:=]\s*['\"]([\w_]+)['\"]",
                    input_str,
                )
                if m:
                    return m.group(1)
        return None


# --------------------------------------------------------------------------- #
# Response usage extractor — defensive across langchain response shapes
# --------------------------------------------------------------------------- #
# Group names that aren't real deployments — skip these when picking the
# model_id so the counter surfaces the underlying NIM/Mistral/etc arm
# instead of the rotator pool name. Keep this set in sync with the
# `_GROUP_NAMES` in domains/llm/rotator/chain/service.py.
_ROTATOR_GROUP_NAMES: frozenset[str] = frozenset({
    "rr-strong", "dd-all", "dd-synth", "dd-reduce-label",
    "dd-keylm", "dd-embed",
})


def _pick_first_real_model(*candidates: Any) -> str | None:
    """Return the first non-empty model string that isn't a rotator group
    name. Group names like `rr-strong` are pool aliases, not deployments;
    we prefer the actual `nvidia_nim/openai/gpt-oss-120b`-style ids."""
    for c in candidates:
        if isinstance(c, str) and c and c not in _ROTATOR_GROUP_NAMES:
            return c
    # Fallback — accept a group name if nothing better is available.
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


def _extract_usage(response: Any) -> tuple[str | None, int, int]:
    """Return (model_id, tokens_in, tokens_out) from a LangChain LLMResult.

    Multiple paths because LangChain shapes vary across model classes:
      - LLMResult.llm_output (legacy dict)
      - AIMessage.usage_metadata + response_metadata (modern ChatModel)
      - response.choices[0].message + response.model (raw OpenAI / LiteLLM)

    LiteLLM Router via langchain-litellm wraps everything in a normal
    ChatGeneration; the actual deployment lands in `response_metadata.
    model_name`. The rotator group name (e.g. `rr-strong`) is what the
    request used, NOT what we want to display; we skip it via
    `_pick_first_real_model`.
    """
    model_candidates: list[Any] = []
    tokens_in = 0
    tokens_out = 0

    # 1. llm_output dict (legacy LLMResult shape)
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

    # 2. Generation[0][0].message — modern ChatModel shape.
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
                # langchain-litellm typically populates model_name with
                # the actual deployment id under response_metadata.
                model_candidates += [
                    rm.get("model_name"),
                    rm.get("model"),
                    rm.get("ls_model_name"),
                    rm.get("ls_provider"),
                ]
                # Some providers put the deployment inside a nested
                # `model_extra` or in the underlying raw response.
                raw = rm.get("response") or rm.get("model_extra") or {}
                if isinstance(raw, dict):
                    model_candidates += [raw.get("model"), raw.get("model_name")]
            # AIMessage.additional_kwargs may also hold the deployment.
            ak = getattr(message, "additional_kwargs", None)
            if isinstance(ak, dict):
                model_candidates += [ak.get("model"), ak.get("model_name")]

    model_id = _pick_first_real_model(*model_candidates)
    return model_id, max(0, tokens_in), max(0, tokens_out)


# --------------------------------------------------------------------------- #
# Retry counter (2026-06-16) — piggy-backs on the LLM-counters HASH so the
# drawer can read everything from one key. A "retry" here means: the orches-
# trator looped back to a phase that was already done — typically detected by
# (a) `write_extraction` overwriting an existing file or extracting an
# arxiv_id not in `top_n`, or (b) PhaseEnforcer pointing back to an
# upstream phase after a downstream phase emitted its terminal artifact.
# --------------------------------------------------------------------------- #
def bump_retry_sync(scan_id: str, phase: str) -> None:
    """Sync-side retry bump. Increments `phase:X:retries` in the per-scan
    counters HASH and `total:retries` for the scan-wide rollup. Best-effort —
    failures only warn-log so callers in fs-tools can't break a write."""
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


# --------------------------------------------------------------------------- #
# Redis writer (sync — called from inside the callback)
# --------------------------------------------------------------------------- #
def _bump_sync(
    *,
    scan_id:   str,
    phase:     str,
    model:     str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Sync Redis pipeline bumping the totals + per-model breakdown.
    Best-effort — failures only warn-log."""
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
        # Per-phase counters
        pipe.hincrby(counters_k, f"{pp}:calls",      1)
        pipe.hincrby(counters_k, f"{pp}:tokens_in",  int(tokens_in))
        pipe.hincrby(counters_k, f"{pp}:tokens_out", int(tokens_out))
        # Scan-wide totals (denormalized so the reader doesn't sum over phases)
        pipe.hincrby(counters_k, "total:calls",      1)
        pipe.hincrby(counters_k, "total:tokens_in",  int(tokens_in))
        pipe.hincrby(counters_k, "total:tokens_out", int(tokens_out))
        # Per-(phase, model) breakdown — Redis can't atomically increment 3
        # fields of a packed string, so use 3 hash fields per model.
        pipe.hincrby(models_k, f"{model}:calls",      1)
        pipe.hincrby(models_k, f"{model}:tokens_in",  int(tokens_in))
        pipe.hincrby(models_k, f"{model}:tokens_out", int(tokens_out))
        # TTLs (best-effort; HEXPIRE isn't always available, refresh whole key)
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


# --------------------------------------------------------------------------- #
# Reader API — async, called from FastAPI
# --------------------------------------------------------------------------- #
async def read_counters(scan_id: str) -> dict[str, Any]:
    """Read all counters for a scan as a structured dict.

    Shape:
        {
          "scan_id": "...",
          "total":   {"calls": N, "tokens_in": X, "tokens_out": Y},
          "by_phase": {
            "discovery":   {"calls": ..., "tokens_in": ..., "tokens_out": ...,
                            "by_model": {"<model>": {"calls": ..., ...}}},
            "triage":      {...},
            ...
          }
        }

    Empty dict (with zero totals) is returned if no counters exist —
    callers don't need to distinguish "scan didn't run" from "scan made
    zero LLM calls" structurally; the n_findings on the scan record does
    that.
    """
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
            return empty

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
        # Group phase fields by phase name.
        phase_fields: dict[str, dict[str, int]] = {}
        for field, val in counters.items():
            if not field.startswith("phase:"):
                continue
            try:
                _, phase, key = field.split(":", 2)
            except ValueError:
                continue
            phase_fields.setdefault(phase, {})[key] = int(val or 0)

        # Fetch per-model breakdown for each phase. Cheap — 4-6 phases,
        # each is one HGETALL.
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


# --------------------------------------------------------------------------- #
# LiteLLM success_callback — the canonical hook for the actual deployment.
# --------------------------------------------------------------------------- #
def _litellm_pull_model_id(kwargs: dict | None, response: Any) -> str:
    """Best deployment id we can derive from LiteLLM's success_callback args.

    Probing order (most specific → fallback):
      1. response.model               (LiteLLM Router sets this to the
                                       deployment that actually answered,
                                       e.g. `nvidia_nim/openai/gpt-oss-120b`)
      2. response._hidden_params.model_id
      3. kwargs['model']              (the original request model — usually
                                       the GROUP name like `rr-strong`)
      4. literal "unknown"
    """
    # Probe 1 — response.model (the canonical LiteLLM field)
    model = getattr(response, "model", None)
    if isinstance(model, str) and model:
        return model
    if isinstance(response, dict):
        v = response.get("model")
        if isinstance(v, str) and v:
            return v
    # Probe 2 — _hidden_params (LiteLLM internals)
    hp = getattr(response, "_hidden_params", None)
    if isinstance(hp, dict):
        v = hp.get("model_id") or hp.get("model")
        if isinstance(v, str) and v:
            return v
    # Probe 3 — kwargs['model'] (the GROUP name)
    if isinstance(kwargs, dict):
        v = kwargs.get("model")
        if isinstance(v, str) and v:
            return v
    return "unknown"


def _litellm_pull_usage(response: Any) -> tuple[int, int]:
    """Pull prompt/completion tokens from a LiteLLM ModelResponse. Returns
    (0, 0) when usage isn't reported."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0
    # LiteLLM's Usage is Pydantic-like with attribute access; dicts are
    # also possible for some providers.
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
    """LiteLLM success callback — fires after every successful chat
    completion (including those from the langchain ChatLiteLLMRouter path).

    Runs synchronously in the same task as `litellm.acompletion`. Reads
    scan_id + phase from contextvars (set by the orchestrator's task
    + the on_tool_start hook). Bumps Redis counters with the REAL
    deployment name (e.g. `nvidia_nim/openai/gpt-oss-120b`) instead of
    the rotator group alias (`rr-strong`).

    Best-effort: any exception is logged + swallowed; the LLM call itself
    has already succeeded.
    """
    try:
        scan_id = get_scan()
        if not scan_id:
            return  # not an RR call
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


# --------------------------------------------------------------------------- #
# Auto-registration with LiteLLM at module import time. Idempotent — re-
# imports don't double-register. The callback no-ops when no scan_id is in
# the current task's context, so DD/YCS workers aren't affected.
# --------------------------------------------------------------------------- #
def _register_with_litellm() -> None:
    """DISABLED 2026-06-16 (msgpack regression).

    Registering this callback caused `litellm` to attach a function
    reference to `response._hidden_params` (callback tracking). That
    reference propagated into AIMessage.response_metadata, and
    langgraph's `InMemorySaver.put_writes` then failed with
    `TypeError: Type is not msgpack serializable: AIMessage` during
    subagent state writes (scan `f8714b75`, 2026-06-16 15:52).

    Until we find a non-mutating extraction path for the real deployment
    (e.g. patching `_RotatorAutoRetryRouter._agenerate` to copy
    `response.model` into `result.llm_output`), the langchain
    `on_llm_end` path in the callback class above does the counting
    with the group-name fallback (`rr-strong`).

    Kept as a no-op so existing callers (`from .llm_counter import
    _register_with_litellm`) don't break, and so v2 work has a clear
    surface to re-enable."""
    return


# Intentionally NOT invoked. Re-enable only after the msgpack-safe
# deployment extraction path is wired (see docstring above).
# _register_with_litellm()


__all__ = [
    "RRLlmCounterCallback",
    "set_scan",
    "get_scan",
    "set_phase",
    "get_phase",
    "read_counters",
]
