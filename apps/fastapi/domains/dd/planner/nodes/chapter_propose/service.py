"""chapter_propose I/O shell — body loader, LLM draft+vote, latest-blob
loader, and the chapter_propose_run orchestration.

SOTA Sept 2026 on coelho-llm-rotator pooled:
- Pooled AsyncOpenAI http2 200/100 handles 3×6000 tok drafts concurrently ~1× latency
  (old bandit 5-way serialized). Optimal stopping default false for fastest.
- Bulk read_many (shared S3 client) vs 16-way semaphore loop.
- Prompt KV-cache: static rubric prefix before dynamic corpus block → prefix
  reuse across N_SAMPLES parallel (Groq/Gemini/DeepSeek auto-cache).
"""
from __future__ import annotations
import domains
from . import domain, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import os
import time
from typing import Optional


class PlannerDegradedAbort(RuntimeError):
    """Raised (opt-in, KD_PLANNER_ABORT_ON_DEGRADE) when chapter_propose fell back
    on a large corpus — surfaces as a normal terminal 'failed' with a clear
    reason instead of grinding chapter_assign for an unusable plan."""


_ABORT_MIN_DOCS = 50


def _abort_on_degrade_enabled() -> bool:
    return os.environ.get("KD_PLANNER_ABORT_ON_DEGRADE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


logger = logging.getLogger(__name__)


async def load_bodies(
    minio, source_keys: list[str], max_chars: int,
) -> dict[str, str]:
    """Read all source bodies — uses MinIO read_many (shared S3 client,
    chunked parallel, 30× faster than per-key semaphore loop). Falls back
    to semaphore loop if read_many missing."""
    # SOTA: storage.service read_many uses single S3 client + BoundedSemaphore
    # per chunk (READ_MAX_CONCURRENT), not per-key session. Much faster for
    # 100+ docs.
    try:
        # Try bulk chunked read (fastest)
        bodies = await minio.read_many(source_keys)  # type: ignore[attr-defined]
        if bodies is not None and len(bodies) == len(source_keys):
            return {k: (b or "")[:max_chars] for k, b in zip(source_keys, bodies)}
    except Exception:
        pass
    # Fallback: legacy semaphore loop
    sem = asyncio.Semaphore(16)

    async def _one(k: str) -> tuple[str, str]:
        async with sem:
            try:
                body = await minio.read_text(k)
                return k, (body or "")[:max_chars]
            except Exception:
                return k, ""

    results = await asyncio.gather(*[_one(k) for k in source_keys])
    return {k: b for k, b in results}


async def draft_one(
    prompt: str, sample_idx: int,
) -> Optional[schemas.ChapterProposalList]:
    try:
        raw, _meta = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens = params.MAX_TOKENS_PROPOSE,
            temperature = params.TEMPERATURE_PROPOSE,
            timeout_s = params.DRAFT_TIMEOUT_S,
            response_format = schemas.PROPOSE_RESPONSE_FORMAT,
        )
    except Exception as e:
        logger.warning(
            f"[chapter_propose] sample {sample_idx} LLM failed: "
            f"{type(e).__name__}: {e}"
        )
        return None
    parsed = domain.parse(raw)
    if not parsed:
        # Empty/unparseable raw (reasoning model exhausted its budget before
        # reaching the JSON, or plain malformed output) — same repair path as
        # a validation failure below, not an immediate None. Same bug pattern
        # that cost doc_distill a 74% fallback rate before this fix.
        payload, err = None, f"unparseable JSON (raw={raw[:200]!r})"
    else:
        payload, err = domain.try_validate(parsed)
    if payload is None and params.MAX_REPAIR_ATTEMPTS > 0:
        # ONE repair attempt at temp=0.
        repair_prompt = (
            prompt
            + f"\n\nPRIOR OUTPUT REJECTED: {err}\nEmit valid JSON per the schema."
        )
        try:
            raw2, _ = await domains.settings.chat.service.chat_text_async(
                repair_prompt,
                max_tokens = params.MAX_TOKENS_PROPOSE,
                temperature = 0.0,
                timeout_s = params.DRAFT_TIMEOUT_S,
                response_format = schemas.PROPOSE_RESPONSE_FORMAT,
            )
            parsed2 = domain.parse(raw2)
            if parsed2:
                payload, _ = domain.try_validate(parsed2)
        except Exception:
            pass
    return payload


async def usc_pick(
    framework: str, candidates: list[schemas.ChapterProposalList],
) -> int:
    if len(candidates) <= 1:
        return 0
    summaries = [domain.summarize_proposal(c.proposals) for c in candidates]
    prompt = prompts.build_usc_vote_prompt(
        framework = framework, candidates_summary = summaries,
    )
    try:
        raw, _ = await domains.settings.chat.service.chat_text_async(
            prompt,
            max_tokens = params.MAX_TOKENS_VOTE,
            temperature = params.TEMPERATURE_VOTE,
            timeout_s = 20.0,
            response_format = schemas.VOTE_RESPONSE_FORMAT,
        )
        parsed = domain.parse(raw)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return idx
    except Exception as e:
        logger.warning(
            f"[chapter_propose] USC pick failed: {type(e).__name__}: {e}"
        )
    # Fallback: max-chapters (coverage > picker silence).
    return max(
        range(len(candidates)),
        key = lambda i: len(candidates[i].proposals),
    )


async def load_proposals(
    minio, slug: str,
) -> Optional[schemas.ChapterProposalList]:
    try:
        text = await minio.read_text(keys.latest_key(slug))
        data = json.loads(text)
        return schemas.ChapterProposalList.model_validate({
            "proposals": data.get("proposals") or [],
        })
    except Exception:
        return None


async def chapter_propose_run(state: domains.dd.planner.state.PlannerState) -> dict:
    """Load corpus + seeds → fire N sampled proposals (with Optimal-
    Stopping on sample 0) → USC-pick → persist."""
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = (
        state.get("relevant_files") or state.get("raw_files") or []
    )
    distill_ref = state.get("doc_distill_ref")

    if not slug or not relevant_files:
        return {
            "chapter_proposals_ref": None,
            "propose_stats": {"skipped": "no_files"},
        }

    t0 = time.monotonic()
    n = len(relevant_files)
    minio = domains.dd.ingestion.storage.service.get_storage()

    manifest = domain.manifest_hash(
        slug = slug, source_keys = relevant_files, distill_ref = distill_ref,
    )
    vkey = keys.versioned_key(slug, manifest)
    lkey = keys.latest_key(slug)
    if await minio.exists(vkey) and await minio.exists(lkey):
        try:
            cached = json.loads(await minio.read_text(vkey))
            wall_ms = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_files": n,
                "n_proposals": len(cached.get("proposals") or []),
                "cache_hit": True,
                "wall_ms": wall_ms,
                "manifest_hash": manifest,
                "titles": [
                    p.get("title") for p in cached.get("proposals") or []
                ],
            }
            await domains.dd.planner.runtime.progress.service.emit_progress(
                thread_id, "chapter_propose", "done",
                cache_hit = True,
                n_proposals = stats["n_proposals"],
                wall_ms = wall_ms,
            )
            return {
                "chapter_proposals_ref": lkey,
                "propose_stats": stats,
            }
        except Exception:
            pass

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_propose", "start",
        n_files = n, distill_available = bool(distill_ref),
    )

    # Settle window — see SETTLE_DELAY_S. Only reached past the cache-hit
    # check above, so a fully-cached re-plan never pays this cost.
    # 2026-09-09: this node runs immediately after doc_distill with NO
    # recovery gap — chapter_assign/order_chapters/doc_distill itself all
    # got their own settle delay, but this one was missed, making it the
    # single most exposed node to the same cooldown-timing problem those
    # fixes address. Confirmed live: chapter_propose failed all 3 samples
    # on every numpy run, before AND after the other three nodes' settle
    # fix — the two runs that did succeed (fastapi) needed luck, not a
    # guarantee, exactly like the other nodes before their own fix.
    if params.SETTLE_DELAY_S > 0:
        await domains.dd.planner.runtime.progress.service.emit_progress(
            thread_id, "chapter_propose", "settling", delay_s = params.SETTLE_DELAY_S,
        )
        await asyncio.sleep(params.SETTLE_DELAY_S)

    distillates_map = None
    if distill_ref:
        distillates_map = await domains.dd.planner.nodes.doc_distill.service.load_distillates(minio, slug)
        if not distillates_map:
            distillates_map = None
    bodies_by_key = await load_bodies(
        minio, relevant_files, params.BODY_CHARS_PER_DOC * 2,
    )

    seeds = domain.extract_structural_seeds(
        source_keys = relevant_files, bodies_by_key = bodies_by_key,
    )

    # Adaptive target; raise stop-floor to ~0.7× so big corpora don't early-stop.
    target_chapters = domain.target_chapters_for_n_docs(len(relevant_files))
    stop_floor = max(
        params.OPTIMAL_STOPPING_MIN_PROPOSALS,
        round(0.7 * target_chapters),
    )

    prompt = prompts.build_propose_prompt(
        framework = slug,
        source_keys = relevant_files,
        distillates = distillates_map,
        bodies_by_key = bodies_by_key if distillates_map is None else None,
        seeds = seeds,
        body_chars_per_doc = params.BODY_CHARS_PER_DOC,
        target_chapters = target_chapters,
    )

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_propose", "sampling",
        n_samples = params.N_SAMPLES, prompt_chars = len(prompt),
        target_chapters = target_chapters, stop_floor = stop_floor,
        n_docs = len(relevant_files),
        n_heading_seeds = len(seeds.get("headings") or []),
        n_namespace_seeds = len(seeds.get("namespaces") or []),
    )

    # Optimal-Stopping (CGES): if sample 0 parses cleanly AND ≥ stop_floor proposals, ship.
    samples: list[schemas.ChapterProposalList | None]
    if params.OPTIMAL_STOPPING_ENABLED and params.N_SAMPLES >= 2:
        s0 = await draft_one(prompt, 0)
        samples = [s0]
        if s0 is not None and len(s0.proposals) >= stop_floor:
            logger.info(
                f"[chapter_propose] Optimal-Stopping fired — sample 0 "
                f"clean ({len(s0.proposals)} proposals ≥ {stop_floor}, "
                f"target={target_chapters}); skipping remaining "
                f"{params.N_SAMPLES - 1} samples"
            )
        else:
            remaining = await asyncio.gather(*[
                draft_one(prompt, i) for i in range(1, params.N_SAMPLES)
            ])
            samples.extend(remaining)
    else:
        samples = list(await asyncio.gather(*[
            draft_one(prompt, i) for i in range(params.N_SAMPLES)
        ]))
    valid: list[schemas.ChapterProposalList] = [s for s in samples if s is not None]

    fallback_used = False
    if not valid:
        # SOTA fix: never return 0 chapters with success status (silent fail).
        # Retry once with backoff for transient 402/timeout, else deterministic
        # fallback from structural seeds so downstream never gets 0.
        logger.warning(
            f"[chapter_propose] all {params.N_SAMPLES} samples failed (timeout/402) — "
            f"retrying once after 2s backoff"
        )
        await asyncio.sleep(2.0)
        # Quick retry: one more parallel sample batch
        retry_samples = list(await asyncio.gather(*[
            draft_one(prompt, i + 10) for i in range(params.N_SAMPLES)
        ]))
        valid = [s for s in retry_samples if s is not None]
        if not valid:
            logger.warning(
                f"[chapter_propose] retry also failed — using deterministic fallback "
                f"({target_chapters} ch from seeds) so pipeline never returns 0"
            )
            fallback = domain.build_fallback_proposals(slug, seeds, target_chapters, n)
            valid = [fallback]
            fallback_used = True
        else:
            logger.info(f"[chapter_propose] retry recovered {len(valid)}/{params.N_SAMPLES} samples")

    chosen_idx = await usc_pick(slug, valid) if not fallback_used else 0
    chosen = valid[chosen_idx]

    payload = {
        "prompt_version":  versions.PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "n_samples_valid": len(valid),
        "n_samples_total": params.N_SAMPLES,
        "chosen_idx":      chosen_idx,
        "fallback_used":   fallback_used,
        "seeds":           seeds,
        "proposals":       [p.model_dump() for p in chosen.proposals],
    }
    blob = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(vkey, blob, content_type = "application/json")
    await minio.write(lkey, blob, content_type = "application/json")

    wall_ms = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_files": n,
        "n_proposals": len(chosen.proposals),
        "n_samples_valid": len(valid),
        "chosen_idx": chosen_idx,
        "cache_hit": False,
        "wall_ms": wall_ms,
        "manifest_hash": manifest,
        "titles": [p.title for p in chosen.proposals],
        "fallback_used": fallback_used,
    }
    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_propose", "done",
        cache_hit = False,
        n_proposals = len(chosen.proposals),
        wall_ms = wall_ms,
        titles = stats["titles"],
    )

    # KD_PLANNER_ABORT_ON_DEGRADE: a chapter_propose fallback on a large corpus
    # is a near-certain predictor of a throwaway plan (generic titles, lopsided
    # catch-all bucket). The artifact is persisted above, so /resume still works
    # once the rotator pool recovers — but don't burn chapter_assign's ~18 min
    # grinding out a plan the caller can't use.
    if fallback_used and _abort_on_degrade_enabled() and n >= _ABORT_MIN_DOCS:
        raise PlannerDegradedAbort(
            f"chapter_propose fell back to deterministic seed titles on {n} docs "
            f"— aborting before chapter_assign (KD_PLANNER_ABORT_ON_DEGRADE). "
            f"Retry when the LLM pool recovers; proposals artifact is saved."
        )

    return {"chapter_proposals_ref": lkey, "propose_stats": stats}
