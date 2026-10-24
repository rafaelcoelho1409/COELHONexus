"""ycs/runtime/llm_counter — pure LLM-response parsing + counter-diff
math. No I/O.

Mirrors `domains.dd.runtime.domain`'s split (same contract/shape,
ported deliberately) — the shapes differ because YCS's Neo4j extraction
goes through a LangChain `on_llm_end` callback (`response.llm_output`),
not a raw provider response object."""
from __future__ import annotations

from typing import Any


def parse_callback_response(response: Any) -> dict[str, Any]:
    """Extract `{tokens_in, tokens_out, reasoning_tokens, model}` from a
    LangChain `on_llm_end` callback's `response` (an `LLMResult`).

    `with_structured_output` goes through this same hook (the wrapper
    is just a Runnable that delegates to the inner `Runnable` —
    verified in langchain-openai 1.x)."""
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
    return {
        "tokens_in":        tokens_in,
        "tokens_out":       tokens_out,
        "reasoning_tokens": reasoning,
        "model":            model,
    }


def diff_usage(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Pure subtraction between two `read_counters()` snapshots — the
    delta is exactly the LLM usage that happened between the two reads
    (e.g. one Ask turn), isolated from the thread's running total.
    Safe because the underlying Redis counters are monotonic
    (`HINCRBY` only, never reset mid-thread).

    2026-09-16: backs the Ask page's per-response usage badge — the
    router snapshots `read_counters(thread_id)` right before and right
    after a turn's graph run and diffs them here, rather than adding a
    second turn-keyed Redis structure alongside the existing thread
    aggregate."""
    def _sub(a: dict[str, Any], b: dict[str, Any]) -> dict[str, int]:
        return {
            k: max(0, int(a.get(k, 0) or 0) - int(b.get(k, 0) or 0))
            for k in ("calls", "tokens_in", "tokens_out", "reasoning_tokens")
        }

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
    models_after  = _models_agg(after)
    by_model: dict[str, dict[str, int]] = {}
    for model, stats_after in models_after.items():
        d = _sub(stats_after, models_before.get(model, {}))
        if d["calls"] > 0:
            by_model[model] = d
    return {"total": total, "by_model": by_model}
