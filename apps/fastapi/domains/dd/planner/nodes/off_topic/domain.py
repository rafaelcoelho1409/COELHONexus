"""off_topic pure helpers (verdict parser); prompt strings + head+tail prep in prompts.py."""
from __future__ import annotations

import re

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_VERDICT_RE = re.compile(r"\b(KEEP|DROP)\b", re.IGNORECASE)


def parse_verdict(text: str) -> bool | None:
    """Parse the LLM's KEEP/DROP verdict. Returns True for KEEP, False for
    DROP, None if unparseable (caller decides fallback).

    Reasoning-tuned models in the rotator's general pool (gpt-oss, deepseek-v4,
    etc.) prepend a <think>...</think> block before the verdict — strip it
    first so a stray "keep"/"drop" mentioned mid-reasoning isn't mistaken for
    the answer. What's left is searched for KEEP/DROP anywhere (not just an
    exact first word — handles markdown wrapping like **KEEP** or a verdict
    embedded in a sentence), taking the LAST match, since a model that restates
    its conclusion states the real answer last.
    """
    if not text:
        return None
    lowered = text.lower()
    if "<think>" in lowered and "</think>" not in lowered:
        # Reasoning got cut off mid-thought (hit max_tokens before closing) —
        # nothing past this point is a real conclusion, don't scan it for a
        # stray "keep"/"drop" mentioned in passing while still reasoning.
        return None
    body = _THINK_BLOCK_RE.sub("", text).strip()
    if not body:
        body = text.strip()
    matches = _VERDICT_RE.findall(body)
    if not matches:
        return None
    return matches[-1].upper() == "KEEP"


def aggregate_verdicts(
    *,
    verdicts: list[tuple[bool, str, str | None, dict]],
    raw_files: list[str],
) -> dict:
    """Fan judge verdicts (in raw_files order) into judge_decisions +
    keep/drop stats + per-deployment usage/reward summary + error
    breakdown. Pure — no I/O, no logging."""
    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    deployment_usage: dict[str, int] = {}
    keep_flags: list[bool] = [False] * len(raw_files)
    for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
        keep_flags[doc_idx] = keep
        dep = (meta or {}).get("deployment") or "?"
        deployment_usage[dep] = deployment_usage.get(dep, 0) + 1
        judge_decisions.append({
            "key":        raw_files[doc_idx],
            "margin":     0.0,
            "verdict":    "KEEP" if keep else "DROP",
            "raw":        raw_resp[:60],   # cap for state payload size
            "error":      err,
            "deployment": dep,
            "latency_s":  (meta or {}).get("latency_s"),
            "reward":     (meta or {}).get("reward"),
            "attempts":   (meta or {}).get("attempts"),
        })
        if err:
            judge_errors.append(err)

    relevant: list[str] = []
    per_file: list[tuple[str, float, str, bool]] = []
    for i, key in enumerate(raw_files):
        keep = keep_flags[i]
        leaf = key.rsplit("/", 1)[-1]
        per_file.append((leaf, 0.0, "llm", keep))
        if keep:
            relevant.append(key)

    llm_kept = sum(1 for d in judge_decisions if d["verdict"] == "KEEP")
    llm_dropped = sum(1 for d in judge_decisions if d["verdict"] == "DROP")

    rewards_by_dep: dict[str, list[float]] = {}
    for d in judge_decisions:
        r = d.get("reward")
        if r is None:
            continue
        rewards_by_dep.setdefault(
            d.get("deployment") or "?", [],
        ).append(float(r))
    deployment_summary = [
        {
            "deployment": dep,
            "calls":      deployment_usage.get(dep, 0),
            "reward_avg": (sum(rewards) / len(rewards)) if rewards else 0.0,
        }
        for dep, rewards in sorted(
            rewards_by_dep.items(),
            key = lambda kv: -deployment_usage.get(kv[0], 0),
        )
    ]

    error_breakdown: dict[str, int] = {}
    for err in judge_errors:
        kind = err.split(":", 1)[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1

    return {
        "relevant":           relevant,
        "per_file":           per_file,
        "judge_decisions":    judge_decisions,
        "judge_errors":       judge_errors,
        "llm_kept":           llm_kept,
        "llm_dropped":        llm_dropped,
        "deployment_summary": deployment_summary,
        "error_breakdown":    error_breakdown,
    }
