"""off_topic I/O shell — one bandit-routed LLM-judge call per doc + the
off_topic_run orchestration (anchor embed, KEEP/DROP per doc, aggregate)."""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from domains.llm.rotator.chain import (
    DD_EMBED_MODEL_NAME,
    chat_judge_bandit_async,
    embed_via_router_async,
)

from ....ingestion.storage import get_storage
from ....resolver import index_by_slug
from ..embed_corpus import load_embeddings
from ...runtime.observability import attach_span_attrs
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import parse_verdict
from .params import (
    JUDGE_BACKOFF_BASE,
    JUDGE_CONCURRENCY,
    JUDGE_MAX_ATTEMPTS,
    JUDGE_MAX_TOKENS,
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
        try:
            async with sem:
                response, meta = await chat_judge_bandit_async(
                    prompt,
                    max_tokens = JUDGE_MAX_TOKENS,
                    temperature = 0.0,
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
            await asyncio.sleep(JUDGE_BACKOFF_BASE ** (attempt + 1))
    if on_complete is not None:
        try:
            await on_complete(keep = True, error = last_error)
        except Exception:
            pass
    return True, last_response, last_error, last_meta


async def off_topic_run(state: PlannerState) -> dict:
    """Embed pos/neg anchors → LLM-judge every doc (bandit, sem-bounded) →
    aggregate KEEP set. Margin = cos(pos) - cos(neg) is telemetry only."""
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
            "off_topic: missing embeddings_ref in state — embed_corpus "
            "must run first"
        )

    entry = index_by_slug().get(slug, {})
    framework_name = entry.get("name") or entry.get("slug") or slug
    framework_category = entry.get("category") or ""
    positive_descriptor = build_positive_descriptor(entry)
    negative_descriptor = NEGATIVE_DESCRIPTOR

    t0 = time.monotonic()
    minio = get_storage()
    await emit_progress(
        thread_id, "off_topic", "start",
        files = len(raw_files), embeddings_ref = embeddings_ref,
    )

    anchor_vecs = await embed_via_router_async(
        [positive_descriptor, negative_descriptor], input_type = "query",
    )
    await emit_progress(
        thread_id, "off_topic", "anchors_embedded",
        positive = positive_descriptor[:200],
        negative = negative_descriptor[:200],
    )
    pos_anchor = np.asarray(anchor_vecs[0], dtype = np.float32)
    neg_anchor = np.asarray(anchor_vecs[1], dtype = np.float32)
    pos_anchor /= max(float(np.linalg.norm(pos_anchor)), 1e-9)
    neg_anchor /= max(float(np.linalg.norm(neg_anchor)), 1e-9)

    blob = await minio.read_bytes(embeddings_ref)
    stored_keys, page_vecs = load_embeddings(blob)

    key_to_idx = {k: i for i, k in enumerate(stored_keys)}
    missing = [k for k in raw_files if k not in key_to_idx]
    if missing:
        raise RuntimeError(
            f"off_topic: {len(missing)} files in raw_files have no "
            f"matching vector in {embeddings_ref!r} — re-run embed_corpus "
            f"(first missing: {missing[0]!r})"
        )
    ordered_idx = np.array(
        [key_to_idx[k] for k in raw_files], dtype = np.int64,
    )
    page_mat = page_vecs[ordered_idx]

    cos_pos = page_mat @ pos_anchor
    cos_neg = page_mat @ neg_anchor
    margins = (cos_pos - cos_neg).astype(np.float64)

    n = len(raw_files)
    keep_mask = np.zeros(n, dtype = bool)

    judge_decisions: list[dict] = []
    judge_errors: list[str] = []
    bodies = await minio.read_many(raw_files)
    sem = asyncio.Semaphore(JUDGE_CONCURRENCY)

    judged_done = {"n": 0, "keep": 0, "drop": 0, "err": 0}
    emit_every = max(1, n // 40)   # ~40 events / run

    async def _on_judge_complete(keep: bool, error: str | None) -> None:
        judged_done["n"] += 1
        if error:
            judged_done["err"] += 1
        elif keep:
            judged_done["keep"] += 1
        else:
            judged_done["drop"] += 1
        if judged_done["n"] % emit_every == 0 or judged_done["n"] == n:
            await emit_progress(
                thread_id, "off_topic", "llm_progress",
                judged = judged_done["n"], total = n,
                llm_keep = judged_done["keep"],
                llm_drop = judged_done["drop"],
                llm_err = judged_done["err"],
            )

    tasks = [
        judge_one(
            sem, framework_name, framework_category, body,
            on_complete = _on_judge_complete,
        )
        for body in bodies
    ]
    verdicts = await asyncio.gather(*tasks)

    deployment_usage: dict[str, int] = {}
    for doc_idx, (keep, raw_resp, err, meta) in enumerate(verdicts):
        keep_mask[doc_idx] = keep
        dep = (meta or {}).get("deployment") or "?"
        deployment_usage[dep] = deployment_usage.get(dep, 0) + 1
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

    relevant: list[str] = []
    per_file: list[tuple[str, float, str, bool]] = []
    cos_kept: list[float] = []
    for i, key in enumerate(raw_files):
        keep = bool(keep_mask[i])
        leaf = key.rsplit("/", 1)[-1]
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

    stats = {
        "kept":                len(relevant),
        "dropped":             n - len(relevant),
        "total":               n,
        "llm_judged":          len(judge_decisions),
        "llm_kept":            llm_kept,
        "llm_dropped":         llm_dropped,
        "llm_errors":          len(judge_errors),
        "llm_error_breakdown": error_breakdown,
        "domain_coherence":    round(domain_coherence, 4),
        "per_file_margins":    per_file,
        "judge_decisions":     judge_decisions,
        "deployment_usage":    deployment_summary,
        "elapsed_ms":          elapsed_ms,
        "anchor_positive":     positive_descriptor,
        "anchor_negative":     negative_descriptor,
        "embeddings_ref":      embeddings_ref,
        "embed_model":         DD_EMBED_MODEL_NAME,
        "judge_concurrency":   JUDGE_CONCURRENCY,
        "judge_router":        "bandit/dd-grader",
    }

    attach_span_attrs("off_topic", {
        "kept":             stats["kept"],
        "dropped":          stats["dropped"],
        "llm_judged":       len(judge_decisions),
        "llm_errors":       len(judge_errors),
        "domain_coherence": stats["domain_coherence"],
        "elapsed_ms":       elapsed_ms,
    })

    top_dep_summary = ", ".join(
        f"{d['deployment'].split('/')[-1]}:{d['calls']}"
        for d in deployment_summary[:3]
    ) or "—"
    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{n} "
        f"(dropped {stats['dropped']}); "
        f"llm judged={len(judge_decisions)} "
        f"(keep={llm_kept} drop={llm_dropped}, "
        f"errors={len(judge_errors)} = "
        f"{dict(sorted(error_breakdown.items()))}); "
        f"top deployments [{top_dep_summary}]; "
        f"coherence={stats['domain_coherence']:.3f}; elapsed={elapsed_ms}ms"
    )
    await emit_progress(
        thread_id, "off_topic", "done",
        kept = len(relevant), dropped = n - len(relevant), total = n,
        llm_judged = len(judge_decisions), llm_keep = llm_kept,
        llm_drop = llm_dropped, llm_err = len(judge_errors),
        llm_error_breakdown = error_breakdown,
        coherence = stats["domain_coherence"],
        wall_ms = elapsed_ms,
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
