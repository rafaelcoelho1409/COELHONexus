"""chapter_propose — LLM proposes 6-15 chapters covering corpus surface.

Pipeline:
  1. Load relevant_files + doc_distill (if available) + raw bodies
     (for small-N pass-through OR for structural seed extraction).
  2. Extract structural seeds (headings, namespaces).
  3. Fire N=3 parallel LLM proposals at temp=0.4 for diversity.
  4. USC-vote pick the best.
  5. ONE repair pass on Pydantic-fail.
  6. Persist as MinIO JSON.

State writes:
  chapter_proposals_ref — MinIO key of the JSON
  propose_stats         — counts + chosen titles for UI
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256
from typing import Optional

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..doc_distill import load_distillates
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import PlannerState

from .constants import (
    _BLOB_PREFIX,
    _BODY_CHARS_PER_DOC,
    _MAX_REPAIR_ATTEMPTS,
    _MAX_TOKENS_PROPOSE,
    _MAX_TOKENS_VOTE,
    _N_SAMPLES,
    _OPTIMAL_STOPPING_ENABLED,
    _OPTIMAL_STOPPING_MIN_PROPOSALS,
    _PROMPT_VERSION,
    _TEMPERATURE_PROPOSE,
    _TEMPERATURE_VOTE,
)
from .service import (
    ChapterProposal,
    ChapterProposalList,
    build_propose_prompt,
    build_usc_vote_prompt,
    extract_structural_seeds,
    summarize_proposal,
)


logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


_PROPOSE_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "chapter_proposal_list",
        "schema": ChapterProposalList.model_json_schema(),
        "strict": False,
    },
}
_VOTE_RESPONSE_FORMAT = {"type": "json_object"}


def _parse(raw: str) -> Optional[dict]:
    if not raw: return None
    m = _JSON_RE.search(raw)
    if not m: return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _try_validate(d: dict) -> tuple[Optional[ChapterProposalList], Optional[str]]:
    try:
        return ChapterProposalList.model_validate(d), None
    except Exception as e:
        return None, str(e)[:300]


async def _load_bodies(minio, source_keys: list[str], max_chars: int) -> dict[str, str]:
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


async def _draft_one(
    prompt: str, sample_idx: int, thread_id: str,
) -> Optional[ChapterProposalList]:
    try:
        raw, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_PROPOSE,
            temperature=_TEMPERATURE_PROPOSE,
            response_format=_PROPOSE_RESPONSE_FORMAT,
        )
    except Exception as e:
        logger.warning(
            f"[chapter_propose] sample {sample_idx} LLM failed: "
            f"{type(e).__name__}: {e}"
        )
        return None
    parsed = _parse(raw)
    if not parsed:
        return None
    payload, err = _try_validate(parsed)
    if payload is None and _MAX_REPAIR_ATTEMPTS > 0:
        # ONE repair attempt at temp=0.
        repair_prompt = (
            prompt
            + f"\n\nPRIOR OUTPUT REJECTED: {err}\nEmit valid JSON per the schema."
        )
        try:
            raw2, _ = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_PROPOSE,
                temperature=0.0,
                response_format=_PROPOSE_RESPONSE_FORMAT,
            )
            parsed2 = _parse(raw2)
            if parsed2:
                payload, _ = _try_validate(parsed2)
        except Exception:
            pass
    return payload


async def _usc_pick(
    framework: str, candidates: list[ChapterProposalList], thread_id: str,
) -> int:
    if len(candidates) <= 1:
        return 0
    summaries = [summarize_proposal(c.proposals) for c in candidates]
    prompt = build_usc_vote_prompt(
        framework=framework, candidates_summary=summaries,
    )
    try:
        raw, _ = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_VOTE,
            temperature=_TEMPERATURE_VOTE,
            response_format=_VOTE_RESPONSE_FORMAT,
        )
        parsed = _parse(raw)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return idx
    except Exception as e:
        logger.warning(
            f"[chapter_propose] USC pick failed: {type(e).__name__}: {e}"
        )
    # Fallback: max number of chapters (preferring coverage when picker fails).
    return max(range(len(candidates)), key=lambda i: len(candidates[i].proposals))


def _manifest_hash(*, slug: str, source_keys: list[str], distill_ref: Optional[str]) -> str:
    h = sha256()
    h.update(_PROMPT_VERSION.encode())
    h.update(slug.encode())
    for k in sorted(source_keys):
        h.update(b"|"); h.update(k.encode())
    h.update(b"|distill="); h.update((distill_ref or "").encode())
    return h.hexdigest()[:16]


def _versioned_key(slug: str, manifest: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_propose/{manifest}.json"


def _latest_key(slug: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/chapter_propose-latest.json"


async def load_proposals(minio, slug: str) -> Optional[ChapterProposalList]:
    try:
        text = await minio.read_text(_latest_key(slug))
        data = json.loads(text)
        return ChapterProposalList.model_validate({
            "proposals": data.get("proposals") or [],
        })
    except Exception:
        return None


@traced("chapter_propose")
async def chapter_propose(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    thread_id = state.get("thread_id") or ""
    relevant_files = state.get("relevant_files") or state.get("raw_files") or []
    distill_ref = state.get("doc_distill_ref")

    if not slug or not relevant_files:
        return {
            "chapter_proposals_ref": None,
            "propose_stats": {"skipped": "no_files"},
        }

    t0 = time.monotonic()
    n = len(relevant_files)
    minio = get_storage()

    # Cache fast-path.
    manifest = _manifest_hash(
        slug=slug, source_keys=relevant_files, distill_ref=distill_ref,
    )
    vkey = _versioned_key(slug, manifest)
    lkey = _latest_key(slug)
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
                "titles": [p.get("title") for p in cached.get("proposals") or []],
            }
            await emit_progress(
                thread_id, "chapter_propose", "done",
                cache_hit=True, n_proposals=stats["n_proposals"], wall_ms=wall_ms,
            )
            return {"chapter_proposals_ref": lkey, "propose_stats": stats}
        except Exception:
            pass

    await emit_progress(
        thread_id, "chapter_propose", "start",
        n_files=n, distill_available=bool(distill_ref),
    )

    # Load distillates (if available) AND raw bodies (always, for seeds).
    distillates_map: Optional[dict[str, dict]] = None
    if distill_ref:
        distillates_map = await load_distillates(minio, slug)
        if not distillates_map:
            distillates_map = None
    bodies_by_key = await _load_bodies(minio, relevant_files, _BODY_CHARS_PER_DOC * 2)

    seeds = extract_structural_seeds(
        source_keys=relevant_files, bodies_by_key=bodies_by_key,
    )

    prompt = build_propose_prompt(
        framework=slug,
        source_keys=relevant_files,
        distillates=distillates_map,
        bodies_by_key=bodies_by_key if distillates_map is None else None,
        seeds=seeds,
        body_chars_per_doc=_BODY_CHARS_PER_DOC,
    )

    await emit_progress(
        thread_id, "chapter_propose", "sampling",
        n_samples=_N_SAMPLES, prompt_chars=len(prompt),
        n_heading_seeds=len(seeds.get("headings") or []),
        n_namespace_seeds=len(seeds.get("namespaces") or []),
    )

    # V2 (2026-05-28) — Optimal-Stopping (CGES). Fire sample 0 first;
    # if it parses cleanly AND emits ≥ _OPTIMAL_STOPPING_MIN_PROPOSALS,
    # ship as-is without firing the remaining N-1. Mirrors outline_sdp's
    # pattern. Disabled via KD_PROPOSE_OPTIMAL_STOPPING=false.
    if _OPTIMAL_STOPPING_ENABLED and _N_SAMPLES >= 2:
        s0 = await _draft_one(prompt, 0, thread_id)
        samples: list = [s0]
        if (
            s0 is not None
            and len(s0.proposals) >= _OPTIMAL_STOPPING_MIN_PROPOSALS
        ):
            logger.info(
                f"[chapter_propose] Optimal-Stopping fired — sample 0 "
                f"clean ({len(s0.proposals)} proposals ≥ "
                f"{_OPTIMAL_STOPPING_MIN_PROPOSALS}); skipping remaining "
                f"{_N_SAMPLES - 1} samples"
            )
        else:
            remaining = await asyncio.gather(*[
                _draft_one(prompt, i, thread_id)
                for i in range(1, _N_SAMPLES)
            ])
            samples.extend(remaining)
    else:
        samples = await asyncio.gather(*[
            _draft_one(prompt, i, thread_id) for i in range(_N_SAMPLES)
        ])
    valid: list[ChapterProposalList] = [s for s in samples if s is not None]

    if not valid:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "chapter_propose", "done",
            error="all_samples_failed", wall_ms=wall_ms,
        )
        return {
            "chapter_proposals_ref": None,
            "propose_stats": {
                "error": "all_samples_failed",
                "n_files": n,
                "wall_ms": wall_ms,
            },
        }

    chosen_idx = await _usc_pick(slug, valid, thread_id)
    chosen = valid[chosen_idx]

    payload = {
        "prompt_version":  _PROMPT_VERSION,
        "framework_slug":  slug,
        "manifest_hash":   manifest,
        "n_samples_valid": len(valid),
        "n_samples_total": _N_SAMPLES,
        "chosen_idx":      chosen_idx,
        "seeds":           seeds,
        "proposals":       [p.model_dump() for p in chosen.proposals],
    }
    blob = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(vkey, blob, content_type="application/json")
    await minio.write(lkey, blob, content_type="application/json")

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
        cache_hit=False, n_proposals=len(chosen.proposals), wall_ms=wall_ms,
        titles=stats["titles"],
    )
    return {"chapter_proposals_ref": lkey, "propose_stats": stats}
