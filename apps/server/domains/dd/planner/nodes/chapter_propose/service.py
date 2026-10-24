"""chapter_propose I/O shell — body loader, LLM draft+vote, latest-blob
loader, and the chapter_propose_run orchestration.

SOTA Sept 2026 on the rotator pooled:
- Pooled httpx http2 200/100 handles 3×6000 tok drafts concurrently ~1× latency
  (old bandit 5-way serialized). Optimal stopping default false for fastest.
- Bulk read_many (shared S3 client) vs 16-way semaphore loop.
- Prompt KV-cache: static rubric prefix before dynamic corpus block → prefix
  reuse across N_SAMPLES parallel (Groq/Gemini/DeepSeek auto-cache).
"""
from __future__ import annotations
import domains, infra
from . import domain, errors, keys, params, prompts, schemas, versions

import asyncio
import json
import logging
import time
from typing import Optional

from pydantic import ValidationError


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
    prompt: str, sample_idx: int, failures: Optional[list] = None,
    max_tokens: Optional[int] = None,
) -> Optional[schemas.ChapterProposalList]:
    """One LLM call, decoded via LangChain's `.with_structured_output()`
    (same pattern as chapter_assign's `_score_call`). A parse/validation
    failure gets up to `params.MAX_REPAIR_ATTEMPTS` repair attempts at
    temp=0, each feeding back the LATEST rejection reason (not just the
    first) so a repeatedly-malformed draft gets progressively more
    specific correction; a network/provider failure on the first call
    returns None immediately — same split as before, now driven by
    `chat_structured_async`'s own exception taxonomy (ValidationError/
    ValueError = reask-eligible, anything else = not).

    `failures` (optional) collects one {sample, stage, kind, msg} per failed
    attempt — a bare `None` return used to hide WHY samples died (asyncio ran
    with 1 valid of 5 and nothing recorded why)."""
    def _note(stage: str, e: Exception) -> None:
        if failures is not None:
            failures.append({
                "sample": sample_idx, "stage": stage,
                "kind": type(e).__name__, "msg": " ".join(str(e).split())[:160],
            })

    budget = max_tokens or params.MAX_TOKENS_PROPOSE
    try:
        payload, _meta = await domains.settings.chat.service.chat_structured_async(
            prompt,
            schemas.ChapterProposalList,
            max_tokens = budget,
            temperature = params.TEMPERATURE_PROPOSE,
            timeout_s = params.DRAFT_TIMEOUT_S,
        )
        return payload
    except (ValidationError, ValueError) as e:
        err = str(e)[:300]
        _note("draft", e)
    except Exception as e:
        _note("draft", e)
        if not domain.is_length_error(e):
            logger.warning(
                f"[chapter_propose] sample {sample_idx} LLM failed: "
                f"{type(e).__name__}: {e}"
            )
            return None
        err = params.LENGTH_REASK_HINT      # output hit max_tokens: ask for a shorter one …
        budget = domain.retry_max_tokens(budget)   # … and allow a longer one (the budget was wrong)

    for repair_attempt in range(params.MAX_REPAIR_ATTEMPTS):
        repair_prompt = (
            prompt + f"\n\nPRIOR OUTPUT REJECTED: {err}\nEmit valid JSON per the schema."
        )
        try:
            payload2, _ = await domains.settings.chat.service.chat_structured_async(
                repair_prompt,
                schemas.ChapterProposalList,
                max_tokens = budget,
                temperature = 0.0,
                timeout_s = params.DRAFT_TIMEOUT_S,
            )
            return payload2
        except (ValidationError, ValueError) as e:
            err = str(e)[:300]
            _note(f"repair{repair_attempt + 1}", e)
        except Exception as e:
            _note(f"repair{repair_attempt + 1}", e)
            if domain.is_length_error(e):
                err = params.LENGTH_REASK_HINT
                budget = domain.retry_max_tokens(budget)
                continue
            logger.warning(
                f"[chapter_propose] sample {sample_idx} repair "
                f"{repair_attempt + 1}/{params.MAX_REPAIR_ATTEMPTS} "
                f"LLM failed: {type(e).__name__}: {e}"
            )
            return None
    return None


async def usc_pick(
    framework: str, candidates: list[schemas.ChapterProposalList],
    scores: Optional[list[dict]] = None, target_chapters: int = 0,
) -> int:
    """Pick the best sample. With `scores` (domain.score_candidates) the evidence decides;
    the LLM vote (Universal Self-Consistency) only breaks near-ties among the shortlist."""
    if len(candidates) <= 1:
        return 0
    short = domain.shortlist(scores, target_chapters) if scores else list(range(len(candidates)))
    if len(short) == 1:
        return short[0]
    summaries = [
        domain.summarize_proposal(candidates[i].proposals, scores[i] if scores else None)
        for i in short
    ]
    prompt = prompts.build_usc_vote_prompt(
        framework = framework, candidates_summary = summaries, target_chapters = target_chapters,
    )
    try:
        vote, _ = await domains.settings.chat.service.chat_structured_async(
            prompt,
            schemas.VotePick,
            max_tokens = params.MAX_TOKENS_VOTE,
            temperature = params.TEMPERATURE_VOTE,
            timeout_s = 20.0,
        )
        if 0 <= vote.chosen_index < len(short):
            return short[vote.chosen_index]
    except Exception as e:
        logger.warning(
            f"[chapter_propose] USC pick failed: {type(e).__name__}: {e}"
        )
    # Fallback: best evidence score, else max-chapters (coverage > picker silence).
    if scores:
        return short[0]
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
    minio = infra.minio.service.get_storage()

    distill_fp = ""
    try:
        distill_fp = domain.fingerprint(
            await domains.dd.planner.nodes.doc_distill.service.load_distillates(minio, slug),
        )
    except Exception:
        pass
    manifest = domain.manifest_hash(
        slug = slug, source_keys = relevant_files, distill_ref = distill_ref,
        distill_fp = distill_fp,
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
        # SETTLE_DELAY_S is now the CAP: proceeds as soon as the Rotator pool has
        # recovered (<=25% of arms cooling, min 10s), full wait if it can't tell.
        settled = await domains.settings.chat.service.wait_for_rotator_settle(params.SETTLE_DELAY_S)
        logger.info(f"[chapter_propose] settle done: {settled}")

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

    # The docs' own structure (parent pages in navigation order, with page counts) —
    # best-effort; "" when the manifest is missing or the corpus has no multi-page families.
    source_structure = ""
    try:
        _m = await domains.dd.ingestion.storage.service.read_framework_manifest(minio, slug)
        _rel = set(relevant_files)
        source_structure = domain.build_source_structure(
            [e for e in (_m or {}).get("entries") or [] if e.get("key") in _rel]
        )
    except Exception as e:
        logger.info(f"[chapter_propose] {slug}: source structure unavailable ({type(e).__name__})")

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
        source_structure = source_structure,
    )

    await domains.dd.planner.runtime.progress.service.emit_progress(
        thread_id, "chapter_propose", "sampling",
        n_samples = params.N_SAMPLES, prompt_chars = len(prompt),
        target_chapters = target_chapters, stop_floor = stop_floor,
        n_docs = len(relevant_files),
        n_heading_seeds = len(seeds.get("headings") or []),
        n_namespace_seeds = len(seeds.get("namespaces") or []),
    )

    draft_tokens = domain.propose_max_tokens(target_chapters)

    # Optimal-Stopping (CGES): if sample 0 parses cleanly AND ≥ stop_floor proposals, ship.
    sample_failures: list[dict] = []
    samples: list[schemas.ChapterProposalList | None]
    if params.OPTIMAL_STOPPING_ENABLED and params.N_SAMPLES >= 2:
        s0 = await draft_one(domain.with_angle(prompt, 0), 0, sample_failures, draft_tokens)
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
                draft_one(domain.with_angle(prompt, i), i, sample_failures, draft_tokens) for i in range(1, params.N_SAMPLES)
            ])
            samples.extend(remaining)
    else:
        samples = list(await asyncio.gather(*[
            draft_one(domain.with_angle(prompt, i), i, sample_failures, draft_tokens) for i in range(params.N_SAMPLES)
        ]))
    valid: list[schemas.ChapterProposalList] = [s for s in samples if s is not None]
    n_first_failed = sum(1 for s_ in samples if s_ is None)
    # ONE extra round, drawn in parallel (two sequential rounds cost ~4 min each on a loaded Rotator):
    #  * top-up — thin best-of-N (fastmcp: 3 of 5, the rest hit the Rotator's wall cap): the evidence pick
    #    needs candidates, so the failed indices are redone (same angles);
    #  * weak batch — even the best draft explains the corpus poorly (fastmcp: all five from one model gave
    #    8-11 chapters at target 24, best score 0.78; langfuse 0.55 / 0.68): more drafts from other arms are
    #    cheap next to a lopsided plan. Skipped when the first round already lost more than
    #    WEAK_MAX_FIRST_FAILED drafts to timeouts — the Rotator is struggling and more big prompts only queue.
    extra_idx: list[int] = []
    if 0 < len(valid) < params.MIN_VALID_SAMPLES and len(samples) == params.N_SAMPLES:
        extra_idx += [i + 10 for i, s_ in enumerate(samples) if s_ is None][:params.MIN_VALID_SAMPLES - len(valid) + 1]
    best_score = None
    if len(valid) >= 2 and params.WEAK_RETRY_SAMPLES > 0 and n_first_failed <= params.WEAK_MAX_FIRST_FAILED:
        try:
            _texts = [
                domain.doc_match_text(k, (distillates_map or {}).get(k), (bodies_by_key or {}).get(k, ""))
                for k in relevant_files
            ]
            best_score = max(sc["score"] for sc in domain.score_candidates(
                [[p.model_dump() for p in v.proposals] for v in valid], _texts, target_chapters))
            if best_score < params.WEAK_BEST_SCORE:
                extra_idx += [20 + a for a in params.WEAK_RETRY_ANGLES[:params.WEAK_RETRY_SAMPLES]]
        except Exception as e:
            logger.warning(f"[chapter_propose] {slug}: weak-batch check skipped ({type(e).__name__}: {e})")
    if extra_idx:
        logger.info(
            f"[chapter_propose] {slug}: {len(valid)}/{len(samples)} valid, best draft score {best_score} "
            f"(weak < {params.WEAK_BEST_SCORE}) — one extra round, drafts {extra_idx}"
        )
        extra = await asyncio.gather(*[
            draft_one(domain.with_angle(prompt, i), i, sample_failures, draft_tokens) for i in extra_idx
        ])
        valid += [s_ for s_ in extra if s_ is not None]
    if sample_failures:
        logger.warning(
            f"[chapter_propose] {slug}: {len(valid)}/{len(samples)} samples valid; "
            f"failures: {sample_failures[:8]}"
        )

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
            draft_one(domain.with_angle(prompt, i + 10), i + 10, sample_failures, draft_tokens) for i in range(params.N_SAMPLES)
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

    cand_scores: list[dict] = []
    if len(valid) > 1 and not fallback_used:
        try:
            texts = [
                domain.doc_match_text(k, (distillates_map or {}).get(k), (bodies_by_key or {}).get(k, ""))
                for k in relevant_files
            ]
            cand_scores = domain.score_candidates(
                [[p.model_dump() for p in v.proposals] for v in valid], texts, target_chapters,
            )
            logger.info(f"[chapter_propose] {slug}: candidate scores {cand_scores}")
        except Exception as e:
            logger.warning(f"[chapter_propose] {slug}: candidate scoring skipped ({type(e).__name__}: {e})")
    chosen_idx = (
        await usc_pick(slug, valid, cand_scores or None, target_chapters) if not fallback_used else 0
    )
    chosen = valid[chosen_idx]

    payload = {
        "prompt_version":  versions.PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "n_samples_valid": len(valid),
        "n_samples_total": params.N_SAMPLES,
        "sample_failures": sample_failures[:20],
        "source_structure": source_structure,
        "chosen_idx":      chosen_idx,
        "candidate_scores": cand_scores,
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
        "n_samples_total": params.N_SAMPLES,
        "sample_failures": sample_failures[:20],
        "has_source_structure": bool(source_structure),
        "chosen_idx": chosen_idx,
        "candidate_scores": cand_scores,
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

    # DD_PLANNER_ABORT_ON_DEGRADE: a chapter_propose fallback on a large corpus
    # is a near-certain predictor of a throwaway plan (generic titles, lopsided
    # catch-all bucket). The artifact is persisted above, so /resume still works
    # once the rotator pool recovers — but don't burn chapter_assign's ~18 min
    # grinding out a plan the caller can't use.
    if fallback_used and params.ABORT_ON_DEGRADE_ENABLED and n >= params.ABORT_MIN_DOCS:
        raise errors.PlannerDegradedAbort(
            f"chapter_propose fell back to deterministic seed titles on {n} docs "
            f"— aborting before chapter_assign (DD_PLANNER_ABORT_ON_DEGRADE). "
            f"Retry when the LLM pool recovers; proposals artifact is saved."
        )

    return {"chapter_proposals_ref": lkey, "propose_stats": stats}
