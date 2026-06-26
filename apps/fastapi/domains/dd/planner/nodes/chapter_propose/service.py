"""chapter_propose I/O shell — body loader, LLM draft+vote, latest-blob
loader, and the chapter_propose_run orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ..doc_distill import load_distillates
from ...runtime.progress import emit_progress
from ...state import PlannerState

from .domain import (
    extract_structural_seeds,
    manifest_hash,
    parse,
    summarize_proposal,
    target_chapters_for_n_docs,
    try_validate,
)
from .keys import latest_key, versioned_key
from .params import (
    BODY_CHARS_PER_DOC,
    MAX_REPAIR_ATTEMPTS,
    MAX_TOKENS_PROPOSE,
    MAX_TOKENS_VOTE,
    N_SAMPLES,
    OPTIMAL_STOPPING_ENABLED,
    OPTIMAL_STOPPING_MIN_PROPOSALS,
    TEMPERATURE_PROPOSE,
    TEMPERATURE_VOTE,
)
from .prompts import build_propose_prompt, build_usc_vote_prompt
from .schemas import (
    PROPOSE_RESPONSE_FORMAT,
    VOTE_RESPONSE_FORMAT,
    ChapterProposalList,
)
from .versions import PROMPT_VERSION


logger = logging.getLogger(__name__)


async def load_bodies(
    minio, source_keys: list[str], max_chars: int,
) -> dict[str, str]:
    """Read all source bodies in parallel — needed for structural seed
    extraction (and full-body pass-through on small N)."""
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
) -> Optional[ChapterProposalList]:
    try:
        raw, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens = MAX_TOKENS_PROPOSE,
            temperature = TEMPERATURE_PROPOSE,
            response_format = PROPOSE_RESPONSE_FORMAT,
        )
    except Exception as e:
        logger.warning(
            f"[chapter_propose] sample {sample_idx} LLM failed: "
            f"{type(e).__name__}: {e}"
        )
        return None
    parsed = parse(raw)
    if not parsed:
        return None
    payload, err = try_validate(parsed)
    if payload is None and MAX_REPAIR_ATTEMPTS > 0:
        # ONE repair attempt at temp=0.
        repair_prompt = (
            prompt
            + f"\n\nPRIOR OUTPUT REJECTED: {err}\nEmit valid JSON per the schema."
        )
        try:
            raw2, _ = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens = MAX_TOKENS_PROPOSE,
                temperature = 0.0,
                response_format = PROPOSE_RESPONSE_FORMAT,
            )
            parsed2 = parse(raw2)
            if parsed2:
                payload, _ = try_validate(parsed2)
        except Exception:
            pass
    return payload


async def usc_pick(
    framework: str, candidates: list[ChapterProposalList],
) -> int:
    if len(candidates) <= 1:
        return 0
    summaries = [summarize_proposal(c.proposals) for c in candidates]
    prompt = build_usc_vote_prompt(
        framework = framework, candidates_summary = summaries,
    )
    try:
        raw, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens = MAX_TOKENS_VOTE,
            temperature = TEMPERATURE_VOTE,
            response_format = VOTE_RESPONSE_FORMAT,
        )
        parsed = parse(raw)
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
) -> Optional[ChapterProposalList]:
    try:
        text = await minio.read_text(latest_key(slug))
        data = json.loads(text)
        return ChapterProposalList.model_validate({
            "proposals": data.get("proposals") or [],
        })
    except Exception:
        return None


async def chapter_propose_run(state: PlannerState) -> dict:
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
    minio = get_storage()

    manifest = manifest_hash(
        slug = slug, source_keys = relevant_files, distill_ref = distill_ref,
    )
    vkey = versioned_key(slug, manifest)
    lkey = latest_key(slug)
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
            await emit_progress(
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

    await emit_progress(
        thread_id, "chapter_propose", "start",
        n_files = n, distill_available = bool(distill_ref),
    )

    distillates_map = None
    if distill_ref:
        distillates_map = await load_distillates(minio, slug)
        if not distillates_map:
            distillates_map = None
    bodies_by_key = await load_bodies(
        minio, relevant_files, BODY_CHARS_PER_DOC * 2,
    )

    seeds = extract_structural_seeds(
        source_keys = relevant_files, bodies_by_key = bodies_by_key,
    )

    # Adaptive target; raise stop-floor to ~0.7× so big corpora don't early-stop.
    target_chapters = target_chapters_for_n_docs(len(relevant_files))
    stop_floor = max(
        OPTIMAL_STOPPING_MIN_PROPOSALS,
        round(0.7 * target_chapters),
    )

    prompt = build_propose_prompt(
        framework = slug,
        source_keys = relevant_files,
        distillates = distillates_map,
        bodies_by_key = bodies_by_key if distillates_map is None else None,
        seeds = seeds,
        body_chars_per_doc = BODY_CHARS_PER_DOC,
        target_chapters = target_chapters,
    )

    await emit_progress(
        thread_id, "chapter_propose", "sampling",
        n_samples = N_SAMPLES, prompt_chars = len(prompt),
        target_chapters = target_chapters, stop_floor = stop_floor,
        n_docs = len(relevant_files),
        n_heading_seeds = len(seeds.get("headings") or []),
        n_namespace_seeds = len(seeds.get("namespaces") or []),
    )

    # Optimal-Stopping (CGES): if sample 0 parses cleanly AND ≥ stop_floor proposals, ship.
    samples: list[ChapterProposalList | None]
    if OPTIMAL_STOPPING_ENABLED and N_SAMPLES >= 2:
        s0 = await draft_one(prompt, 0)
        samples = [s0]
        if s0 is not None and len(s0.proposals) >= stop_floor:
            logger.info(
                f"[chapter_propose] Optimal-Stopping fired — sample 0 "
                f"clean ({len(s0.proposals)} proposals ≥ {stop_floor}, "
                f"target={target_chapters}); skipping remaining "
                f"{N_SAMPLES - 1} samples"
            )
        else:
            remaining = await asyncio.gather(*[
                draft_one(prompt, i) for i in range(1, N_SAMPLES)
            ])
            samples.extend(remaining)
    else:
        samples = list(await asyncio.gather(*[
            draft_one(prompt, i) for i in range(N_SAMPLES)
        ]))
    valid: list[ChapterProposalList] = [s for s in samples if s is not None]

    if not valid:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "chapter_propose", "done",
            error = "all_samples_failed", wall_ms = wall_ms,
        )
        return {
            "chapter_proposals_ref": None,
            "propose_stats": {
                "error": "all_samples_failed",
                "n_files": n,
                "wall_ms": wall_ms,
            },
        }

    chosen_idx = await usc_pick(slug, valid)
    chosen = valid[chosen_idx]

    payload = {
        "prompt_version":  PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "n_samples_valid": len(valid),
        "n_samples_total": N_SAMPLES,
        "chosen_idx":      chosen_idx,
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
    }
    await emit_progress(
        thread_id, "chapter_propose", "done",
        cache_hit = False,
        n_proposals = len(chosen.proposals),
        wall_ms = wall_ms,
        titles = stats["titles"],
    )
    return {"chapter_proposals_ref": lkey, "propose_stats": stats}
