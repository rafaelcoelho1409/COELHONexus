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
import domains
from . import domain, params, prompts

import asyncio
import logging
import random
import time


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
    prompt = prompts.build_judge_prompt(framework_name, framework_category, body)
    last_error: str | None = None
    last_response: str = ""
    last_meta: dict = {}
    for attempt in range(params.JUDGE_MAX_ATTEMPTS):
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
                response, meta = await domains.settings.chat.service.chat_text_async(
                    prompt,
                    max_tokens = params.JUDGE_MAX_TOKENS + (200 if retrying_unparseable else 0),
                    temperature = 0.4 if retrying_unparseable else 0.0,
                    timeout_s = params.JUDGE_TIMEOUT_S,
                    expected_pattern = r"^(KEEP|DROP)$",
                )
            last_response = response
            last_meta = meta
            verdict = domain.parse_verdict(response)
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
        if attempt < params.JUDGE_MAX_ATTEMPTS - 1:
            # Jittered backoff — avoids synchronized retry storm on shared rotator arms
            base = params.JUDGE_BACKOFF_BASE ** (attempt + 1)
            jitter = 1.0 + random.random() * 0.3
            await asyncio.sleep(base * jitter)
    if on_complete is not None:
        try:
            await on_complete(keep = True, error = last_error)
        except Exception:
            pass
    return True, last_response, last_error, last_meta


async def off_topic_run(state: domains.dd.planner.state.PlannerState) -> dict:
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

    entry = domains.dd.resolver.service.index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    framework_category = entry.get("category") or ""

    t0 = time.monotonic()
    minio = domains.dd.ingestion.storage.service.get_storage()
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "off_topic", "start",
        files = len(raw_files),
    )

    n = len(raw_files)
    bodies = await minio.read_many(raw_files)
    sem = asyncio.Semaphore(params.JUDGE_CONCURRENCY)

    # Dedupe identical judge inputs before spending LLM calls — the judge prompt
    # is a pure function of head_tail_truncate(body), so pages that collapse to the
    # same prompt (empty pages, stub redirects, scaffold duplicates, mirrored dumps)
    # share one verdict. Group first, judge unique prompts concurrently, fan out.
    groups: dict[str, list[int]] = {}
    for i, body in enumerate(bodies):
        groups.setdefault(prompts.head_tail_truncate(body or ""), []).append(i)
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
            await domains.dd.planner.runtime.progress.service.emit_progress(
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

    agg = domain.aggregate_verdicts(verdicts = verdicts, raw_files = raw_files)
    relevant = agg["relevant"]
    per_file = agg["per_file"]
    judge_decisions = agg["judge_decisions"]
    judge_errors = agg["judge_errors"]
    llm_kept = agg["llm_kept"]
    llm_dropped = agg["llm_dropped"]
    deployment_summary = agg["deployment_summary"]
    error_breakdown = agg["error_breakdown"]

    domain_coherence = 0.0
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Retrieve descriptors for stats (no embedding)
    positive_descriptor = prompts.build_positive_descriptor(entry)
    negative_descriptor = params.NEGATIVE_DESCRIPTOR

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
        "judge_concurrency":   params.JUDGE_CONCURRENCY,
        "judge_router":        "coelho-llm-rotator",
    }

    domains.dd.planner.runtime.observability.service.attach_span_attrs("off_topic", {
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
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "off_topic", "done",
        kept = len(relevant), dropped = n - len(relevant), total = n,
        llm_judged = n_to_judge, llm_deduped = n_deduped,
        llm_keep = llm_kept,
        llm_drop = llm_dropped, llm_err = len(judge_errors),
        llm_error_breakdown = error_breakdown,
        wall_ms = elapsed_ms,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
