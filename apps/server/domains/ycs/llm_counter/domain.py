"""ycs/llm_counter — pure counter-diff math. No I/O. Response parsing is shared
(`domains.settings.llm_usage.domain.extract_usage`)."""
from __future__ import annotations

from typing import Any


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
