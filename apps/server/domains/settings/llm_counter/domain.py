"""llm_counter domain — pure helpers of the LLM-usage counters; no I/O.

One parser for the token usage and model of an LLM response: a LangChain `LLMResult` (the `on_llm_end`
callback payload — LangChain's native `AIMessage.usage_metadata` first, then `llm_output`), a bare
`AIMessage` (what `ChatOpenAI.ainvoke` returns) and a raw provider / LiteLLM response (`.usage` + `.model`).
Then the counter layout (`split_counters` ... `build_usage`), the per-context usage payload
(`payload`, `empty_payload`), the usage delta of one turn (`diff_usage`) and the failure classes."""
from __future__ import annotations
from . import entities

from typing import Any


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return getattr(obj, "__dict__", {}) or {}


def _tokens_from_usage(usage: dict) -> tuple[int, int, int]:
    """(in, out, reasoning) from an OpenAI-style usage dict (`prompt_tokens`/`input_tokens`, ...)."""
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    details = _as_dict(usage.get("completion_tokens_details") or usage.get("output_tokens_details"))
    reasoning = int(usage.get("reasoning_tokens") or details.get("reasoning_tokens") or 0)
    return tokens_in, tokens_out, reasoning


def pick_first_real_model(*candidates: Any) -> str | None:
    """The first non-empty model string among `candidates`."""
    for c in candidates:
        if isinstance(c, str) and c:
            return c
    return None


def _message_of(response: Any) -> Any:
    """The `AIMessage` carrying the usage: `response` itself when it is a bare message, else the first generation's."""
    if hasattr(response, "response_metadata"):
        return response
    generations = getattr(response, "generations", None) or []
    if generations and generations[0]:
        return getattr(generations[0][0], "message", None)
    return None


def extract_usage(response: Any) -> entities.Usage:
    """Parse `response` into a `Usage`. Later, more specific sources win when they carry a number:
    raw `.usage`, then `llm_output` (`token_usage` / `usage`), then the `AIMessage.usage_metadata` (a bare
    message, or the first generation's). The model is the first real name among those sources, in that order."""
    tokens_in = tokens_out = reasoning = 0
    models: list[Any] = []

    raw_usage = _as_dict(_get(response, "usage"))
    if raw_usage:
        tokens_in, tokens_out, reasoning = _tokens_from_usage(raw_usage)
    models.append(_get(response, "model"))

    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        models += [llm_output.get("model_name"), llm_output.get("model"), llm_output.get("model_id")]
        usage = _as_dict(llm_output.get("token_usage") or llm_output.get("usage"))
        if usage:
            t_in, t_out, t_reason = _tokens_from_usage(usage)
            tokens_in, tokens_out, reasoning = t_in or tokens_in, t_out or tokens_out, t_reason or reasoning

    message = _message_of(response)
    if message is not None:
        meta = getattr(message, "usage_metadata", None)
        if isinstance(meta, dict):
            tokens_in = int(meta.get("input_tokens") or tokens_in)
            tokens_out = int(meta.get("output_tokens") or tokens_out)
            out_details = meta.get("output_token_details")
            if isinstance(out_details, dict):
                reasoning = int(out_details.get("reasoning") or reasoning)
        response_meta = getattr(message, "response_metadata", None) or {}
        if isinstance(response_meta, dict):
            models += [
                response_meta.get("model_name"), response_meta.get("model"),
                response_meta.get("ls_model_name"), response_meta.get("ls_provider"),
            ]
            raw = response_meta.get("response") or response_meta.get("model_extra") or {}
            if isinstance(raw, dict):
                models += [raw.get("model"), raw.get("model_name")]
        extra = getattr(message, "additional_kwargs", None)
        if isinstance(extra, dict):
            models += [extra.get("model"), extra.get("model_name")]

    return entities.Usage(
        model = pick_first_real_model(*models),
        tokens_in = max(0, tokens_in),
        tokens_out = max(0, tokens_out),
        reasoning_tokens = max(0, reasoning),
    )


# ---- the shared Redis counter layout (DD thread counters, YCS extract / Ask counters) ------------------------------
# One hash per run: `total:<f>`, `node:<node_id>:<f>`, `errkind:<kind>` (+ DD's `stage`); one hash per node:
# `<model>:<f>`. `<f>` is a COUNT_FIELDS name, or `errors` (DD only).

COUNT_FIELDS = ("calls", "tokens_in", "tokens_out", "reasoning_tokens")


def decode_hash(raw: dict | None) -> dict[str, str]:
    """A redis `HGETALL` reply (bytes or str) as `{str: str}`."""
    return {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in (raw or {}).items()
    }


def split_counters(counters: dict[str, str]) -> tuple[dict[str, int], dict[str, int], dict[str, dict[str, int]]]:
    """`(total, error_kinds, node_fields)` of a decoded run hash; `node_fields` is `{node_id: {field: n}}`."""
    total = {f: int(counters.get(f"total:{f}", 0) or 0) for f in (*COUNT_FIELDS, "errors")}
    error_kinds = {k.split(":", 1)[1]: int(v or 0) for k, v in counters.items() if k.startswith("errkind:")}
    node_fields: dict[str, dict[str, int]] = {}
    for field, val in counters.items():
        if not field.startswith("node:"):
            continue
        try:
            _, node_id, key = field.split(":", 2)
        except ValueError:
            continue
        node_fields.setdefault(node_id, {})[key] = int(val or 0)
    return total, error_kinds, node_fields


def parse_models(raw: dict | None) -> dict[str, dict[str, int]]:
    """A node's `HGETALL` model hash as `{model: {field: n}}`."""
    by_model: dict[str, dict[str, int]] = {}
    for key, val in decode_hash(raw).items():
        try:
            model, field = key.rsplit(":", 1)
        except ValueError:
            continue
        by_model.setdefault(model, {})[field] = int(val or 0)
    return by_model


def build_usage(counters: dict[str, str], by_model: dict[str, dict[str, dict[str, int]]]) -> dict[str, Any]:
    """The `{stage, total, error_kinds, by_node}` payload the usage drawer renders. `by_model` is `{node_id: parse_models(...)}`."""
    total, error_kinds, node_fields = split_counters(counters)
    return {
        "stage": counters.get("stage"),
        "total": total,
        "error_kinds": error_kinds,
        "by_node": {
            node_id: {**{f: int(fields.get(f, 0)) for f in (*COUNT_FIELDS, "errors")}, "by_model": by_model.get(node_id, {})}
            for node_id, fields in node_fields.items()
        },
    }


def payload(spec: entities.Namespace, run_id: str, usage: dict[str, Any]) -> dict[str, Any]:
    """The usage drawer's payload for one run, shaped by the context's `Namespace`: its id field, and — only when it
    counts failures — `stage`, `errors` and `error_kinds`. `usage` is a `build_usage` result."""
    counts = (*COUNT_FIELDS, "errors") if spec.errors else COUNT_FIELDS
    out: dict[str, Any] = {spec.id_field: run_id}
    if spec.errors:
        out["stage"] = usage["stage"]
    out["total"] = {f: usage["total"][f] for f in counts}
    if spec.errors:
        out["error_kinds"] = usage["error_kinds"]
    out["by_node"] = {
        node_id: {**{f: node[f] for f in counts}, "by_model": node["by_model"]}
        for node_id, node in usage["by_node"].items()
    }
    return out


def empty_payload(spec: entities.Namespace, run_id: str) -> dict[str, Any]:
    """The payload of a run with no counters."""
    counts = (*COUNT_FIELDS, "errors")
    return payload(spec, run_id, {"stage": None, "total": dict.fromkeys(counts, 0), "error_kinds": {}, "by_node": {}})


def model_from_response(response: Any, fallback: str | None = None) -> str:
    """The model a call is counted under: `fallback` when it is a `provider/model` id, else the response's own model,
    else LiteLLM's hidden params, else `fallback` / 'unknown'."""
    model = _get(response, "model")
    if isinstance(fallback, str) and "/" in fallback:
        return fallback
    if isinstance(model, str) and model:
        return model
    hidden = _get(response, "_hidden_params")
    if isinstance(hidden, dict):
        for key in ("model_id", "model"):
            val = hidden.get(key)
            if isinstance(val, str) and val:
                return val
    return fallback or "unknown"


def failure_kind(exc: BaseException) -> str:
    """Short label of a failed LLM call for the error counters: '504' (Rotator cascade cap), 'timeout' (client backstop),
    'rate_limit', or the exception class."""
    msg = str(exc)
    if "Error code: 504" in msg or "504" in msg[:80]:
        return "504"
    if type(exc).__name__ == "ChatTimeoutError" or "backstop" in msg:
        return "timeout"
    if "429" in msg[:80] or "rate" in msg[:80].lower():
        return "rate_limit"
    return type(exc).__name__


def diff_usage(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Pure subtraction between two usage payloads — the delta is exactly the LLM usage that happened between the two
    reads (e.g. one Ask turn), isolated from the thread's running total. Safe because the Redis counters are monotonic
    (`HINCRBY` only, never reset mid-thread). Backs the Ask page's per-response usage badge."""
    def _sub(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
        return {k: max(0, int(a.get(k, 0) or 0) - int(b.get(k, 0) or 0)) for k in COUNT_FIELDS}

    total = _sub(after.get("total") or {}, before.get("total") or {})

    def _models_agg(snapshot: dict[str, Any]) -> dict[str, dict[str, int]]:
        agg: dict[str, dict[str, int]] = {}
        for node in (snapshot.get("by_node") or {}).values():
            for model, stats in (node.get("by_model") or {}).items():
                acc = agg.setdefault(model, {})
                for k, v in (stats or {}).items():
                    acc[k] = acc.get(k, 0) + int(v or 0)
        return agg

    models_before = _models_agg(before)
    by_model: dict[str, dict[str, int]] = {}
    for model, stats_after in _models_agg(after).items():
        delta = _sub(stats_after, models_before.get(model, {}))
        if delta["calls"] > 0:
            by_model[model] = delta
    return {"total": total, "by_model": by_model}
