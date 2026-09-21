"""doc_distill I/O shell — per-doc LLM distillation, latest-blob loader,
and the doc_distill_run orchestration.

SOTA Sept 2026 — fully on coelho-llm-rotator pooled client:
- I/O decoupled from LLM semaphore (MinIO reads bulk-fetched before LLM fan-out).
- Pooled AsyncOpenAI (http2, 200/100 limits) replaces per-call ChatOpenAI construction
  → eliminates per-call TLS + client alloc, reuses keep-alive across 135+ docs.
- CONCURRENCY 24 (was 8) — rotator's simple-shuffle + allowed_fails absorbs 429s,
  off_topic already proved 20-way saturates without the old 36% blowup.
- Jittered backoff on transient retries to avoid thundering-herd on shared arms.
"""
from __future__ import annotations
import domains
from . import domain, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import random
import time
from typing import Optional


logger = logging.getLogger(__name__)


# 2026-09-09: "rate_limit" removed. A RouterRateLimitError here means
# litellm's Router already checked every deployment in the pool and found
# none available — benched for cooldown_time=120s (chain/service.py's
# _get_router(), COELHOLLMRotator repo), not a one-off per-deployment
# blip. The old 2-5s backoff before retrying could never land after a
# deployment actually became free again, so both retries were guaranteed
# to fail too — confirmed live on langchain-langgraph-deepagents: 133/166
# docs hit this, each burning a doomed retry cycle before falling back to
# the same fallback distillate anyway. Dropping it here means those calls
# fail straight to fallback — identical outcome, ~8s less wasted wait per
# occurrence. timeout/connection stay retryable — those genuinely can
# clear within seconds.
_TRANSIENT_REASONS = frozenset({"timeout", "connection"})


async def distill_one(
    sem: asyncio.Semaphore,
    framework: str,
    source_key: str,
    body: str | None,
) -> tuple[str, Optional[schemas.DocDistillate], int, bool, Optional[str]]:
    """Returns (key, distillate, wall_ms, used_fallback, failure_reason).

    `body` is pre-fetched outside the semaphore; None means read_fail.
    Empty body → None (no LLM). Only LLM hop is semaphore-gated.
    """
    async with sem:
        t0 = time.monotonic()
        if body is None:
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

        prompt = prompts.build_prompt(framework, source_key, body)
        distillate: Optional[schemas.DocDistillate] = None
        failure_reason: Optional[str] = None
        last_raw = ""
        last_deployment = "?"
        meta: dict | None = None

        # Retry only transient errors — pooled rotator rotates arm, jitter avoids herd.
        for attempt in range(params.MAX_TRANSIENT_RETRIES + 1):
            try:
                raw, meta = await domains.settings.chat.service.chat_text_async(
                    prompt,
                    max_tokens = params.MAX_TOKENS,
                    temperature = params.TEMPERATURE,
                    timeout_s = params.TIMEOUT_S,
                    response_format = schemas.DISTILL_RESPONSE_FORMAT,
                )
                last_raw = raw or ""
                last_deployment = (meta or {}).get("deployment") or "?"
                parsed = domain.parse(raw)
                if not parsed:
                    # Empty/unparseable raw — often a reasoning model that spent
                    # its whole token budget on a <think> block and never reached
                    # the JSON. Same repair path as a validation failure below,
                    # not an immediate drop to parse_fail: one reask with the
                    # rejection reason fed back gives it a chance to recover.
                    distillate = None
                    err = f"unparseable JSON (raw={raw[:200]!r})"
                    failure_reason = "parse_fail"
                else:
                    distillate, err = domain.try_validate(parsed)
                    if distillate is None:
                        failure_reason = "validate_fail"
                if distillate is None and params.MAX_REPAIR_ATTEMPTS > 0:
                    repair_prompt = (
                        prompt
                        + f"\n\nPRIOR OUTPUT was REJECTED: {err}\n"
                        + f"Emit valid JSON exactly per the schema above."
                    )
                    raw2, meta2 = await domains.settings.chat.service.chat_text_async(
                        repair_prompt,
                        max_tokens = params.MAX_TOKENS,
                        temperature = 0.0,
                        timeout_s = params.TIMEOUT_S,
                        response_format = schemas.DISTILL_RESPONSE_FORMAT,
                    )
                    last_raw = raw2 or ""
                    last_deployment = (meta2 or {}).get("deployment") or last_deployment
                    parsed2 = domain.parse(raw2)
                    if parsed2:
                        distillate, _ = domain.try_validate(parsed2)
                if distillate is not None:
                    failure_reason = None
                    break   # success
                break       # parse_fail/validate_fail keep their reason; no further retry
            except Exception as e:
                failure_reason = domain.classify_error(e)
                is_transient = failure_reason in _TRANSIENT_REASONS
                can_retry = attempt < params.MAX_TRANSIENT_RETRIES
                logger.warning(
                    f"[doc_distill] {source_key} attempt {attempt + 1}: "
                    f"{failure_reason} ({type(e).__name__}: {e})"
                )
                if is_transient and can_retry:
                    backoff = params.RETRY_BACKOFF_S[
                        min(attempt, len(params.RETRY_BACKOFF_S) - 1)
                    ]
                    # Jitter 0-20% to avoid synchronized retry storm on shared rotator arms
                    jitter = 1.0 + random.random() * 0.2
                    await asyncio.sleep(backoff * jitter)
                    continue
                break

        # Failed LLM (with content) → deterministic fallback so doc still flows downstream.
        used_fallback = False
        if distillate is None:
            distillate = domain.build_fallback_distillate(source_key, body)
            used_fallback = True
            logger.info(
                f"[doc_distill] {source_key}: distill failed "
                f"({failure_reason or 'unknown'}, deployment={last_deployment}, "
                f"raw={last_raw[:120]!r}) — using deterministic fallback "
                f"distillate (doc kept, not dropped)"
            )

        wall_ms = int((time.monotonic() - t0) * 1000)
        return source_key, distillate, wall_ms, used_fallback, failure_reason


async def load_distillates(minio, slug: str) -> dict:
    """Reads the latest doc_distill blob. Used by chapter_propose and
    chapter_assign. Returns {} on miss."""
    try:
        text = await minio.read_text(keys.latest_key(slug))
        data = json.loads(text)
        return data.get("distillates") or {}
    except Exception:
        return {}


async def doc_distill_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Pass-through small-N corpora; otherwise fan out parallel
    distillation, persist as MinIO JSON, write the latest pointer.

    SOTA: bulk MinIO read decoupled from LLM semaphore → semaphore gates only
    the network-bound LLM hop. LLM calls ride the pooled AsyncOpenAI keep-alive
    path, so 24 concurrent saturate the rotator without pool exhaustion.
    """
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
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "doc_distill", "start",
        n_files = n,
        pass_through_threshold = params.PASS_THROUGH_THRESHOLD,
    )

    if n <= params.PASS_THROUGH_THRESHOLD:   # small-N pass-through; downstream uses raw bodies
        wall_ms = int((time.monotonic() - t0) * 1000)
        await domains.dd.planner.runtime.progress.service.emit_progress(
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

    minio = domains.dd.ingestion.storage.service.get_storage()
    manifest = domain.manifest_hash(slug = slug, relevant_files = relevant_files)
    vkey = keys.versioned_key(slug, manifest)
    lkey = keys.latest_key(slug)
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
            await domains.dd.planner.runtime.progress.service.emit_progress(
                thread_id, "doc_distill", "done",
                cache_hit = True,
                n_distilled = stats["n_distilled"],
                wall_ms = wall_ms,
            )
            return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
        except Exception:
            pass

    # Settle window — see SETTLE_DELAY_S. Only reached past the cache-hit
    # check above, so a fully-cached re-plan never pays this cost.
    if params.SETTLE_DELAY_S > 0:
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "doc_distill", "settling", delay_s = params.SETTLE_DELAY_S,
        )
        await asyncio.sleep(params.SETTLE_DELAY_S)

    # Bulk MinIO read BEFORE semaphore — keep LLM concurrency pure.
    await domains.dd.planner.runtime.progress.service.emit_progress(thread_id, "doc_distill", "loading_bodies", n_files = n)
    t_read = time.monotonic()
    try:
        bodies = await minio.read_many(relevant_files)  # type: ignore[attr-defined]
        # read_many returns list[str|None] in same order; fallback to per-key if missing
        if bodies is None or len(bodies) != n:
            raise RuntimeError("read_many length mismatch")
        body_map: dict[str, str | None] = {k: b for k, b in zip(relevant_files, bodies)}
    except Exception as e:
        # Fallback: sequential reads (still outside LLM semaphore)
        logger.warning(f"[doc_distill] read_many failed ({type(e).__name__}: {e}), falling back to per-key reads")
        body_map = {}
        # Concurrent reads without LLM semaphore — I/O bound only
        async def _read_one(k: str) -> tuple[str, str | None]:
            try:
                return k, await minio.read_text(k)
            except Exception:
                return k, None
        read_results = await asyncio.gather(*[_read_one(k) for k in relevant_files])
        body_map = dict(read_results)

    read_ms = int((time.monotonic() - t_read) * 1000)
    n_empty = sum(1 for k in relevant_files if not (body_map.get(k) or "").strip())
    n_read_fail = sum(1 for k in relevant_files if body_map.get(k) is None)
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "doc_distill", "bodies_loaded",
        read_ms = read_ms, n_read_fail = n_read_fail, n_empty = n_empty,
    )

    sem = asyncio.Semaphore(params.CONCURRENCY)
    # Progress emitter for LLM phase — every ~10% or 20 docs
    llm_done = {"n": 0}
    emit_every = max(1, n // 10)
    lock = asyncio.Lock()

    orig_distill_one = distill_one

    async def _tracked_distill(k: str, b: str | None):
        res = await orig_distill_one(sem, slug, k, b)
        async with lock:
            llm_done["n"] += 1
            if llm_done["n"] % emit_every == 0 or llm_done["n"] == n:
                try:
                    await domains.dd.planner.runtime.progress.service.emit_progress(
                        thread_id, "doc_distill", "llm_progress",
                        distilled = llm_done["n"], total = n,
                    )
                except Exception:
                    pass
        return res

    tasks = [
        _tracked_distill(k, body_map.get(k)) for k in relevant_files
    ]
    results = await asyncio.gather(*tasks, return_exceptions=False)

    distillates: dict[str, dict] = {}
    failures: list[dict] = []       # no content at all (read fail / empty)
    fallbacks: list[dict] = []      # content present but LLM distill failed
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
        by_reason: dict[str, list[str]] = {}
        for fb in fallbacks:
            by_reason.setdefault(fb["reason"], []).append(fb["key"])
        for r, fallback_keys in by_reason.items():
            logger.warning(
                f"[doc_distill] {slug}: {len(fallback_keys)} doc(s) fell back due "
                f"to {r}: {fallback_keys[:10]}"
            )

    payload = {
        "prompt_version": versions.PROMPT_VERSION,
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
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(vkey, blob, content_type="application/json")
    await minio.write(lkey, blob, content_type="application/json")

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
        "read_ms": read_ms,
    }
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "doc_distill", "done",
        cache_hit = False,
        n_distilled = len(distillates),
        n_failed = len(failures),
        n_fallback = len(fallbacks),
        failure_reasons = failure_reasons,
        wall_ms = wall_ms,
        read_ms = read_ms,
    )
    return {"doc_distill_ref": lkey, "doc_distill_stats": stats}
