"""Substep 3 — off_topic: two-stage filter (cheap cosine prefilter +
LLM-as-judge on the middle band).

Design (2026-05-17 night — supersedes the GMM/Otsu percentile cut):
the embedding margin is a CHEAP PREFILTER, not the verdict. Per DCLM
2026 ablations + RAGAS small-corpus guidance, the right shape for a
100-2000 doc corpus is: cleave the obvious cases with similarity
geometry, then ask the big LLM to judge the uncertain middle band per
page. No percentile-based amputation — every doc gets the right amount
of scrutiny.

  1. Load pre-computed unit-norm vectors (NIM dd-embed, via embed_corpus).
  2. Embed positive + negative anchors (input_type="query"); compute
     margin = cos(page, pos) - cos(page, neg).
  3. CLEAVE:
       margin >= _MARGIN_KEEP_FLOOR  → KEEP (clearly on-topic; no LLM)
       margin <= _MARGIN_DROP_CEIL   → DROP (clearly meta-content; no LLM)
       otherwise                     → LLM-AS-JUDGE per page
  4. For middle-band docs, ask the dd-all rotator (big-model group) a
     binary KEEP/DROP question with a strict rubric. Bounded concurrency
     via asyncio.Semaphore so we don't blow past 40 RPM cumulatively.
  5. Combine cheap-cleave verdicts + LLM verdicts → final relevant_files.

Stats payload reports each path's verdict counts + per-file decisions so
the operator can spot-check the LLM's judgments and tune the cleave
thresholds.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

import numpy as np

from ...resolver import _index_by_slug
from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import (
    DD_EMBED_MODEL_NAME,
    embed_via_router_async,
)

from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState
from ..embed_corpus import load_embeddings
from .constants import (
    _JUDGE_CONCURRENCY,
    _NEGATIVE_DESCRIPTOR,
    _RERANK_THRESHOLD,
)
from .service import (
    _build_positive_descriptor,
    _judge_one,
    off_topic_via_rerank,
)


def _rerank_mode_active() -> bool:
    """Phase A (2026-05-23): KD_OFF_TOPIC_USE_RERANK=1 → cross-encoder rerank
    replaces LLM-judge-per-doc. Default off — set the env to enable per pod."""
    return os.environ.get("KD_OFF_TOPIC_USE_RERANK", "0") == "1"


logger = logging.getLogger(__name__)


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    raw_files = state.get("raw_files") or []
    embeddings_ref = state.get("embeddings_ref") or ""
    if not slug or not raw_files:
        return {
            "relevant_files": list(raw_files),
            "off_topic_stats": {
                "kept": len(raw_files), "dropped": 0,
                "skipped": "no input",
            },
        }
    if not embeddings_ref:
        raise RuntimeError(
            "off_topic: missing embeddings_ref in state — embed_corpus must "
            "run first"
        )

    entry = _index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    framework_category = entry.get("category") or ""
    positive_descriptor = _build_positive_descriptor(entry)
    negative_descriptor = _NEGATIVE_DESCRIPTOR

    t0 = time.monotonic()
    minio = get_storage()
    await emit_progress(
        thread_id, "off_topic", "start",
        files=len(raw_files), embeddings_ref=embeddings_ref,
    )

    # ── Embed both anchors as queries (single rotator call). ────────────
    anchor_vecs = await embed_via_router_async(
        [positive_descriptor, negative_descriptor], input_type="query",
    )
    await emit_progress(
        thread_id, "off_topic", "anchors_embedded",
        positive=positive_descriptor[:200],
        negative=negative_descriptor[:200],
    )
    pos_anchor = np.asarray(anchor_vecs[0], dtype=np.float32)
    neg_anchor = np.asarray(anchor_vecs[1], dtype=np.float32)
    pos_anchor /= max(float(np.linalg.norm(pos_anchor)), 1e-9)
    neg_anchor /= max(float(np.linalg.norm(neg_anchor)), 1e-9)

    # ── Load pre-computed unit-norm corpus matrix. ─────────────────────
    blob = await minio.read_bytes(embeddings_ref)
    stored_keys, page_vecs = load_embeddings(blob)

    key_to_idx = {k: i for i, k in enumerate(stored_keys)}
    missing = [k for k in raw_files if k not in key_to_idx]
    if missing:
        raise RuntimeError(
            f"off_topic: {len(missing)} files in raw_files have no matching "
            f"vector in {embeddings_ref!r} — re-run embed_corpus "
            f"(first missing: {missing[0]!r})"
        )
    ordered_idx = np.array([key_to_idx[k] for k in raw_files], dtype=np.int64)
    page_mat = page_vecs[ordered_idx]

    cos_pos = page_mat @ pos_anchor
    cos_neg = page_mat @ neg_anchor
    margins = (cos_pos - cos_neg).astype(np.float64)

    # 2026-05-17 night: pure LLM-as-Judge — no cosine cleave. Every doc
    # gets the bandit-routed big-LLM verdict. Margins stay in the stats
    # payload as TELEMETRY only (operator can correlate margin to LLM
    # verdict to spot calibration drift between cheap + expensive signals).
    #
    # 2026-05-23 Phase A: KD_OFF_TOPIC_USE_RERANK=1 swaps to cross-encoder
    # rerank fast-path (NIM `nvidia/llama-nemotron-rerank-1b-v2` batched at
    # 256 passages/call + sigmoid threshold). 280 s → ~15-25 s on 777 docs.
    # See domains/dd/planner/off_topic/service.py:off_topic_via_rerank.
    n = len(raw_files)
    keep_mask = np.zeros(n, dtype=bool)
    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    bodies = await minio.read_many(raw_files)
    rerank_used = _rerank_mode_active()
    rerank_scores: list[float] | None = None

    if rerank_used:
        # ── Cross-encoder rerank fast-path (Phase A) ────────────────────
        await emit_progress(
            thread_id, "off_topic", "rerank_start",
            files=n, threshold=_RERANK_THRESHOLD,
        )
        keep_list, rerank_scores = await off_topic_via_rerank(
            framework_descriptor=positive_descriptor,
            doc_bodies=list(bodies),
            threshold=_RERANK_THRESHOLD,
        )
        for doc_idx, (key, body, keep, prob) in enumerate(
            zip(raw_files, bodies, keep_list, rerank_scores)
        ):
            keep_mask[doc_idx] = keep
            judge_decisions.append({
                "key":           key,
                "margin":        float(margins[doc_idx]),
                "verdict":       "KEEP" if keep else "DROP",
                "raw":           f"sigmoid={prob:.3f}",
                "error":         None,
                "deployment":    "nim/nemotron-rerank-1b-v2",
                "latency_s":     None,
                "reward":        None,
                "attempts":      1,
                "rerank_score":  float(prob),
            })
        await emit_progress(
            thread_id, "off_topic", "rerank_done",
            files=n,
            kept=int(keep_mask.sum()),
            dropped=int(n - keep_mask.sum()),
        )
    else:
        # ── Legacy: LLM-as-judge on EVERY doc, bandit-routed. ──────────
        sem = asyncio.Semaphore(_JUDGE_CONCURRENCY)

        # Shared counter so we can emit live "judged N/M" progress as
        # each future resolves (futures complete in arbitrary order with
        # asyncio.gather, so accumulate across the gathered set).
        judged_done = {"n": 0, "keep": 0, "drop": 0, "err": 0}
        _EMIT_EVERY = max(1, n // 40)   # ~40 progress events / run

        async def _on_judge_complete(keep: bool, error: str | None) -> None:
            judged_done["n"] += 1
            if error:
                judged_done["err"] += 1
            elif keep:
                judged_done["keep"] += 1
            else:
                judged_done["drop"] += 1
            if judged_done["n"] % _EMIT_EVERY == 0 or judged_done["n"] == n:
                await emit_progress(
                    thread_id, "off_topic", "llm_progress",
                    judged=judged_done["n"], total=n,
                    llm_keep=judged_done["keep"],
                    llm_drop=judged_done["drop"],
                    llm_err=judged_done["err"],
                )

        tasks = [
            _judge_one(
                sem, framework_name, framework_category, body,
                on_complete=_on_judge_complete,
            )
            for body in bodies
        ]
        verdicts = await asyncio.gather(*tasks)

        # Bandit deployment usage tally — which models actually answered.
        for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
            keep_mask[doc_idx] = keep
            dep = (meta or {}).get("deployment") or "?"
            judge_decisions.append({
                "key":        raw_files[doc_idx],
                "margin":     float(margins[doc_idx]),
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

    # ── Compute outputs + observability ────────────────────────────────
    relevant: list[str] = []
    per_file: list[tuple[str, float, str, bool]] = []
    cos_kept: list[float] = []
    for i, key in enumerate(raw_files):
        keep = bool(keep_mask[i])
        leaf = key.rsplit("/", 1)[-1]
        # decision_source is always "llm" in the pure-LLM-judge design;
        # kept in the tuple for forward-compat with future cleave revivals.
        per_file.append((leaf, round(float(margins[i]), 4), "llm", keep))
        if keep:
            relevant.append(key)
            cos_kept.append(float(cos_pos[i]))

    domain_coherence = (
        sum(cos_kept) / len(cos_kept) if cos_kept else 0.0
    )
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    llm_kept = sum(1 for d in judge_decisions if d["verdict"] == "KEEP")
    llm_dropped = sum(1 for d in judge_decisions if d["verdict"] == "DROP")

    # Bandit telemetry — top deployments by usage + reward avg per deployment.
    # Derived from judge_decisions so both LLM-judge and rerank paths populate
    # consistent telemetry (rerank path = single nim/nemotron-rerank-1b-v2 entry).
    deployment_usage: dict[str, int] = {}
    for d in judge_decisions:
        dep = d.get("deployment") or "?"
        deployment_usage[dep] = deployment_usage.get(dep, 0) + 1
    rewards_by_dep: dict[str, list[float]] = {}
    for d in judge_decisions:
        r = d.get("reward")
        if r is None:
            continue
        rewards_by_dep.setdefault(d.get("deployment") or "?", []).append(float(r))
    deployment_summary = [
        {
            "deployment": dep,
            "calls":      deployment_usage.get(dep, 0),
            "reward_avg": (sum(rewards) / len(rewards)) if rewards else 0.0,
        }
        for dep, rewards in sorted(
            rewards_by_dep.items(),
            key=lambda kv: -deployment_usage.get(kv[0], 0),
        )
    ]
    # Ensure all picked deployments show in the summary, even those without
    # reward signal (rerank path is the main case — no per-call reward).
    for dep, calls in sorted(
        deployment_usage.items(), key=lambda kv: -kv[1],
    ):
        if not any(s["deployment"] == dep for s in deployment_summary):
            deployment_summary.append({
                "deployment": dep, "calls": calls, "reward_avg": 0.0,
            })

    stats = {
        "kept":              len(relevant),
        "dropped":           n - len(relevant),
        "total":             n,
        "llm_judged":        len(judge_decisions),
        "llm_kept":          llm_kept,
        "llm_dropped":       llm_dropped,
        "llm_errors":        len(judge_errors),
        "domain_coherence":  round(domain_coherence, 4),
        "per_file_margins":  per_file,
        "judge_decisions":   judge_decisions,
        "deployment_usage":  deployment_summary,
        "elapsed_ms":        elapsed_ms,
        "anchor_positive":   positive_descriptor,
        "anchor_negative":   negative_descriptor,
        "embeddings_ref":    embeddings_ref,
        "embed_model":       DD_EMBED_MODEL_NAME,
        "judge_concurrency": _JUDGE_CONCURRENCY,
        "judge_router":      "pareto-bandit/dd-grader",
        # Phase A: which classification path actually ran on this invocation.
        "mode":              "rerank" if rerank_used else "llm_judge",
        "rerank_threshold":  _RERANK_THRESHOLD if rerank_used else None,
        "rerank_scores":     rerank_scores if rerank_used else None,
    }

    try:
        from opentelemetry import trace as _otel_trace
        span = _otel_trace.get_current_span()
        span.set_attribute("off_topic.kept", stats["kept"])
        span.set_attribute("off_topic.dropped", stats["dropped"])
        span.set_attribute("off_topic.llm_judged", len(judge_decisions))
        span.set_attribute("off_topic.llm_errors", len(judge_errors))
        span.set_attribute("off_topic.domain_coherence", stats["domain_coherence"])
        span.set_attribute("off_topic.elapsed_ms", elapsed_ms)
    except Exception:
        pass

    # Build an error-type breakdown so the operator can see WHAT's failing.
    error_breakdown: dict[str, int] = {}
    for err in judge_errors:
        # err is "ExceptionType: message" — bucket by the prefix.
        kind = err.split(":", 1)[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1
    stats["llm_error_breakdown"] = error_breakdown

    top_dep_summary = ", ".join(
        f"{d['deployment'].split('/')[-1]}:{d['calls']}"
        for d in deployment_summary[:3]
    ) or "—"
    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{n} "
        f"(dropped {stats['dropped']}); "
        f"llm judged={len(judge_decisions)} (keep={llm_kept} drop={llm_dropped}, "
        f"errors={len(judge_errors)} = {dict(sorted(error_breakdown.items()))}); "
        f"top deployments [{top_dep_summary}]; "
        f"coherence={stats['domain_coherence']:.3f}; elapsed={elapsed_ms}ms"
    )
    await emit_progress(
        thread_id, "off_topic", "done",
        kept=len(relevant), dropped=n - len(relevant), total=n,
        llm_judged=len(judge_decisions), llm_keep=llm_kept,
        llm_drop=llm_dropped, llm_err=len(judge_errors),
        llm_error_breakdown=error_breakdown,
        coherence=stats["domain_coherence"],
        wall_ms=elapsed_ms,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
