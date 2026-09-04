"""off_topic I/O shell — LLM-only KEEP/DROP per doc (embed_corpus removed).

SOTA Sept 2026 on coelho-llm-rotator pooled client:
- Pooled AsyncOpenAI (http2, 200/100) replaces per-call ChatOpenAI alloc.
- CONCURRENCY 24 saturates pool, no embedding work.
- Dedupe via head_tail_truncate collapses mirrored dumps → single LLM call.
- Jittered backoff, short 15s timeout for 1-token verdict.
- Prompt static prefix (framework) before dynamic body → KV-cache reuse
  (Groq/Gemini/DeepSeek auto-cache, 2-3× TTFT after warmup).
- Embed corpus fully removed: margins/coherence now 0/null (LLM authoritative).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time

import numpy as np

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ....resolver import index_by_slug
from ...runtime.observability import attach_span_attrs
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import parse_verdict
from .params import (
    JUDGE_BACKOFF_BASE,
    JUDGE_CONCURRENCY,
    JUDGE_MAX_ATTEMPTS,
    JUDGE_MAX_TOKENS,
    JUDGE_TIMEOUT_S,
    NEGATIVE_DESCRIPTOR,
)
from .prompts import build_judge_prompt, build_positive_descriptor


logger = logging.getLogger(__name__)


async def judge_one(
    sem: asyncio.Semaphore,
    framework_name: str,
    framework_category: str,
    body: str,
    on_complete = None,
) -> tuple[bool, str, str | None, dict]:
    """ONE bandit-routed LLM-judge call. Returns (keep, raw, error, meta).
    Defaults to KEEP on any failure (quality-over-speed rule).
    `on_complete` (optional) is invoked per judgment for live progress."""
    prompt = build_judge_prompt(framework_name, framework_category, body)
    last_error: str | None = None
    last_response: str = ""
    last_meta: dict = {}
    for attempt in range(JUDGE_MAX_ATTEMPTS):
        # A prior unparseable_verdict means that attempt's model likely burned
        # its whole token budget on an unfinished <think> block. temperature=0.0
        # is deterministic per-model, and when the rotator's alive pool has
        # shrunk (other providers cooling down from 402/429), a retry has a
        # real chance of landing back on the same deployment — reproducing the
        # identical truncated output. Widen the budget and break determinism
        # on the retry instead of just hoping for a different bandit draw.
        retrying_unparseable = last_error == "unparseable_verdict"
        try:
            async with sem:
                response, meta = await chat_judge_bandit_async(
                    prompt,
                    max_tokens = JUDGE_MAX_TOKENS + (200 if retrying_unparseable else 0),
                    temperature = 0.4 if retrying_unparseable else 0.0,
                    timeout_s = JUDGE_TIMEOUT_S,
                    expected_pattern = r"^(KEEP|DROP)$",
                )
            last_response = response
            last_meta = meta
            verdict = parse_verdict(response)
            if verdict is not None:
                if on_complete is not None:
                    try:
                        await on_complete(keep = verdict, error = None)
                    except Exception:
                        pass
                return verdict, response, None, meta
            last_error = "unparseable_verdict"
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
        if attempt < JUDGE_MAX_ATTEMPTS - 1:
            # Jittered backoff — avoids synchronized retry storm on shared rotator arms
            base = JUDGE_BACKOFF_BASE ** (attempt + 1)
            jitter = 1.0 + random.random() * 0.3
            await asyncio.sleep(base * jitter)
    if on_complete is not None:
        try:
            await on_complete(keep = True, error = last_error)
        except Exception:
            pass
    return True, last_response, last_error, last_meta


async def off_topic_run(state: PlannerState) -> dict:
    """LLM-judge every doc (sem-bounded) → aggregate KEEP set. No embeddings."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    raw_files = state.get("raw_files") or []
    if not slug or not raw_files:
        return {
            "relevant_files": list(raw_files),
            "off_topic_stats": {
                "kept": len(raw_files), "dropped": 0,
                "skipped": "no input",
            },
        }

    entry = index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    framework_category = entry.get("category") or ""

    t0 = time.monotonic()
    minio = get_storage()
    await emit_progress(
        thread_id, "off_topic", "start",
        files = len(raw_files),
    )

    n = len(raw_files)
    keep_mask = np.zeros(n, dtype = bool)

    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    bodies = await minio.read_many(raw_files)
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    # Dedupe identical judge inputs before spending LLM calls — the judge prompt
    # is a pure function of head_tail_truncate(body), so pages that collapse to the
    # same prompt (empty pages, stub redirects, scaffold duplicates, mirrored dumps)
    # share one verdict. Group first, judge unique prompts concurrently, fan out.
    from .prompts import head_tail_truncate
    groups: dict[str, list[int]] = {}
    for i, body in enumerate(bodies):
        groups.setdefault(head_tail_truncate(body or ""), []).append(i)
    unique_keys = list(groups.keys())
    n_deduped = n - len(unique_keys)

    judged_done = {"n": 0, "keep": 0, "drop": 0, "err": 0}
    n_to_judge = len(unique_keys)
    emit_every = max(1, n_to_judge // 40)   # ~40 events / run

    async def _on_judge_complete(keep: bool, error: str | None) -> None:
        judged_done["n"] += 1
        if error:
            judged_done["err"] += 1
        elif keep:
            judged_done["keep"] += 1
        else:
            judged_done["drop"] += 1
        if judged_done["n"] % emit_every == 0 or judged_done["n"] == n_to_judge:
            await emit_progress(
                thread_id, "off_topic", "llm_progress",
                judged = judged_done["n"], total = n_to_judge,
                deduped = n_deduped,
                llm_keep = judged_done["keep"],
                llm_drop = judged_done["drop"],
                llm_err = judged_done["err"],
            )

    tasks = [
        judge_one(
            sem, framework_name, framework_category, key,
            on_complete = _on_judge_complete,
        )
        for key in unique_keys
    ]
    unique_verdicts = await asyncio.gather(*tasks)

    # Fan unique verdicts back out to the original doc order. Duplicates inherit
    # the same verdict + meta (deployment usage counted once on the unique call).
    verdicts: list = [None] * n
    for key, res in zip(unique_keys, unique_verdicts):
        for doc_idx in groups[key]:
            verdicts[doc_idx] = res

    deployment_usage: dict[str, int] = {}
    for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
        keep_mask[doc_idx] = keep
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
        keep = bool(keep_mask[i])
        leaf = key.rsplit("/", 1)[-1]
        per_file.append((leaf, 0.0, "llm", keep))
        if keep:
            relevant.append(key)

    domain_coherence = 0.0
    elapsed_ms = int((time.monotonic() - t0) * 1000)
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

    # Retrieve descriptors for stats (no embedding)
    positive_descriptor = build_positive_descriptor(entry)
    negative_descriptor = NEGATIVE_DESCRIPTOR

    stats = {
        "kept":                len(relevant),
        "dropped":             n - len(relevant),
        "total":               n,
        "llm_judged":          n_to_judge,
        "llm_deduped":         n_deduped,
        "llm_kept":            llm_kept,
        "llm_dropped":         llm_dropped,
        "llm_errors":          len(judge_errors),
        "llm_error_breakdown": error_breakdown,
        "domain_coherence":    0.0,
        "per_file_margins":    per_file,
        "judge_decisions":     judge_decisions,
        "deployment_usage":    deployment_summary,
        "elapsed_ms":          elapsed_ms,
        "anchor_positive":     positive_descriptor,
        "anchor_negative":     negative_descriptor,
        "judge_concurrency":   JUDGE_CONCURRENCY,
        "judge_router":        "coelho-llm-rotator",
    }

    attach_span_attrs("off_topic", {
        "kept":             stats["kept"],
        "dropped":          stats["dropped"],
        "llm_judged":       n_to_judge,
        "llm_deduped":      n_deduped,
        "llm_errors":       len(judge_errors),
        "domain_coherence": 0.0,
        "elapsed_ms":       elapsed_ms,
    })

    top_dep_summary = ", ".join(
        f"{d['deployment'].split('/')[-1]}:{d['calls']}"
        for d in deployment_summary[:3]
    ) or "—"
    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{n} "
        f"(dropped {stats['dropped']}); "
        f"llm judged={n_to_judge} (deduped {n_deduped}) "
        f"(keep={llm_kept} drop={llm_dropped}, "
        f"errors={len(judge_errors)} = "
        f"{dict(sorted(error_breakdown.items()))}); "
        f"top deployments [{top_dep_summary}]; "
        f"elapsed={elapsed_ms}ms (LLM-only, no embed)"
    )
    await emit_progress(
        thread_id, "off_topic", "done",
        kept = len(relevant), dropped = n - len(relevant), total = n,
        llm_judged = n_to_judge, llm_deduped = n_deduped,
        llm_keep = llm_kept,
        llm_drop = llm_dropped, llm_err = len(judge_errors),
        llm_error_breakdown = error_breakdown,
        wall_ms = elapsed_ms,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
