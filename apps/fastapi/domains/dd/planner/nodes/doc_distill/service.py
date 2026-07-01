"""doc_distill I/O shell — per-doc LLM distillation, latest-blob loader,
and the doc_distill_run orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import (
    build_fallback_distillate,
    classify_error,
    manifest_hash,
    parse,
    try_validate,
)
from .keys import latest_key, versioned_key
from .params import (
    CONCURRENCY,
    MAX_REPAIR_ATTEMPTS,
    MAX_TOKENS,
    MAX_TRANSIENT_RETRIES,
    PASS_THROUGH_THRESHOLD,
    RETRY_BACKOFF_S,
    TEMPERATURE,
)
from .prompts import build_prompt
from .schemas import DISTILL_RESPONSE_FORMAT, DocDistillate
from .versions import PROMPT_VERSION


logger = logging.getLogger(__name__)


_TRANSIENT_REASONS = frozenset({"rate_limit", "timeout", "connection"})


async def distill_one(
    sem: asyncio.Semaphore,
    minio,
    framework: str,
    source_key: str,
) -> tuple[str, Optional[DocDistillate], int, bool, Optional[str]]:
    """Returns (key, distillate, wall_ms, used_fallback, failure_reason); distillate=None only for unreadable docs, never for LLM failures (uses fallback instead)."""
    async with sem:
        t0 = time.monotonic()
        try:
            body = await minio.read_text(source_key)
        except Exception as e:
            logger.warning(
                f"[doc_distill] failed to read {source_key}: "
                f"{type(e).__name__}: {e}"
            )
            return (
                source_key, None,
                int((time.monotonic() - t0) * 1000),
                False, "read_fail",
            )

        if not (body or "").strip():
            return (
                source_key, None,
                int((time.monotonic() - t0) * 1000),
                False, "empty_body",
            )

        prompt = build_prompt(framework, source_key, body)
        distillate: Optional[DocDistillate] = None
        failure_reason: Optional[str] = None

        # Retry only transient errors — bandit rotates to a different deployment, so a saturated arm typically clears in one attempt.
        for attempt in range(MAX_TRANSIENT_RETRIES + 1):
            try:
                # dd-reduce-label = non-reasoning pool (no <think>, 2-3× faster).
                raw, _meta = await chat_judge_bandit_async(
                    prompt,
                    max_tokens = MAX_TOKENS,
                    temperature = TEMPERATURE,
                    response_format = DISTILL_RESPONSE_FORMAT,
                    dd_process = "dd-reduce-label",
                )
                parsed = parse(raw)
                if not parsed:
                    failure_reason = "parse_fail"
                distillate, err = try_validate(parsed)
                if distillate is None and MAX_REPAIR_ATTEMPTS > 0:
                    repair_prompt = (
                        prompt
                        + f"\n\nPRIOR OUTPUT was REJECTED: {err}\n"
                        + f"Emit valid JSON exactly per the schema above."
                    )
                    raw2, _ = await chat_judge_bandit_async(
                        repair_prompt,
                        max_tokens = MAX_TOKENS,
                        temperature = 0.0,
                        response_format = DISTILL_RESPONSE_FORMAT,
                        dd_process = "dd-reduce-label",
                    )
                    parsed2 = parse(raw2)
                    if parsed2:
                        distillate, _ = try_validate(parsed2)
                if distillate is not None:
                    failure_reason = None
                    break   # success
                failure_reason = "validate_fail"
                break       # validation failures don't get retried
            except Exception as e:
                failure_reason = classify_error(e)
                is_transient = failure_reason in _TRANSIENT_REASONS
                can_retry = attempt < MAX_TRANSIENT_RETRIES
                logger.warning(
                    f"[doc_distill] {source_key} attempt {attempt + 1}: "
                    f"{failure_reason} ({type(e).__name__}: {e})"
                )
                if is_transient and can_retry:
                    backoff = RETRY_BACKOFF_S[
                        min(attempt, len(RETRY_BACKOFF_S) - 1)
                    ]
                    await asyncio.sleep(backoff)
                    continue
                break

        # Failed LLM (with content) → deterministic fallback so doc still flows downstream.
        used_fallback = False
        if distillate is None:
            distillate = build_fallback_distillate(source_key, body)
            used_fallback = True
            logger.info(
                f"[doc_distill] {source_key}: distill failed "
                f"({failure_reason or 'unknown'}) — using deterministic "
                f"fallback distillate (doc kept, not dropped)"
            )

        wall_ms = int((time.monotonic() - t0) * 1000)
        return source_key, distillate, wall_ms, used_fallback, failure_reason


async def load_distillates(minio, slug: str) -> dict:
    """Reads the latest doc_distill blob. Used by chapter_propose and
    chapter_assign. Returns {} on miss."""
    try:
        text = await minio.read_text(latest_key(slug))
        data = json.loads(text)
        return data.get("distillates") or {}
    except Exception:
        return {}


async def doc_distill_run(state: PlannerState) -> dict:
    """Pass-through small-N corpora; otherwise fan out parallel
    distillation, persist as MinIO JSON, write the latest pointer."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = (
        state.get("relevant_files") or state.get("raw_files") or []
    )

    if not slug or not relevant_files:
        return {
            "doc_distill_ref": None,
            "doc_distill_stats": {
                "skipped": "no_files",
                "n_files": 0,
            },
        }

    n = len(relevant_files)
    t0 = time.monotonic()
    await emit_progress(
        thread_id, "doc_distill", "start",
        n_files = n,
        pass_through_threshold = PASS_THROUGH_THRESHOLD,
    )

    if n <= PASS_THROUGH_THRESHOLD:   # small-N pass-through; downstream uses raw bodies
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "doc_distill", "done",
            skipped = "pass_through_small_n",
            n_files = n, wall_ms = wall_ms,
        )
        return {
            "doc_distill_ref": None,
            "doc_distill_stats": {
                "skipped": "pass_through_small_n",
                "n_files": n,
                "wall_ms": wall_ms,
            },
        }

    minio = get_storage()
    manifest = manifest_hash(slug = slug, relevant_files = relevant_files)
    vkey = versioned_key(slug, manifest)
    lkey = latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached_text = await minio.read_text(vkey)
            cached = json.loads(cached_text)
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_files": n,
                "n_distilled": len(
                    (cached or {}).get("distillates") or {},
                ),
                "manifest_hash": manifest,
                "cache_hit": True,
                "wall_ms": wall_ms,
            }
            await emit_progress(
                thread_id, "doc_distill", "done",
                cache_hit = True,
                n_distilled = stats["n_distilled"],
                wall_ms = wall_ms,
            )
            return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
        except Exception:
            pass

    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = [
        distill_one(sem, minio, slug, k) for k in relevant_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions = False)

    distillates: dict[str, dict] = {}
    failures: list[dict] = []       # no content at all (read fail / empty)
    fallbacks: list[dict] = []      # content present but LLM distill failed
    # bucket-counter so the planner UI / logs can
    # distinguish rate-limit pressure (operational, retry will eventually
    # succeed) from genuine schema failures (prompt drift, model bug).
    failure_reasons: dict[str, int] = {}
    for k, dist, _wall, used_fb, reason in results:
        if dist is not None:
            distillates[k] = dist.model_dump()
            if used_fb:
                fallbacks.append({"key": k, "reason": reason or "unknown"})
                r = reason or "unknown"
                failure_reasons[r] = failure_reasons.get(r, 0) + 1
        else:
            failures.append({"key": k, "reason": reason or "unknown"})
            r = reason or "unknown"
            failure_reasons[r] = failure_reasons.get(r, 0) + 1

    if fallbacks:
        # Group fallback keys by reason for a single-line log that's
        # legible at WARNING level (one entry per bucket).
        by_reason: dict[str, list[str]] = {}
        for fb in fallbacks:
            by_reason.setdefault(fb["reason"], []).append(fb["key"])
        for r, keys in by_reason.items():
            logger.warning(
                f"[doc_distill] {slug}: {len(keys)} doc(s) fell back due "
                f"to {r}: {keys[:10]}"
            )

    payload = {
        "prompt_version": PROMPT_VERSION,
        "manifest_hash":  manifest,
        "framework_slug": slug,
        "distillates":    distillates,
        "n_files":        n,
        "n_distilled":    len(distillates),
        "n_failed":       len(failures),
        "failures":       failures[:20],  # cap for blob size
        "n_fallback":     len(fallbacks),
        "fallbacks":      fallbacks[:20],
        "failure_reasons": failure_reasons,
    }
    blob = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(vkey, blob, content_type = "application/json")
    await minio.write(lkey, blob, content_type = "application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_files": n,
        "n_distilled": len(distillates),
        "n_failed": len(failures),
        "n_fallback": len(fallbacks),
        "failure_reasons": failure_reasons,
        "manifest_hash": manifest,
        "cache_hit": False,
        "wall_ms": wall_ms,
    }
    await emit_progress(
        thread_id, "doc_distill", "done",
        cache_hit = False,
        n_distilled = len(distillates),
        n_failed = len(failures),
        n_fallback = len(fallbacks),
        failure_reasons = failure_reasons,
        wall_ms = wall_ms,
    )
    return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
