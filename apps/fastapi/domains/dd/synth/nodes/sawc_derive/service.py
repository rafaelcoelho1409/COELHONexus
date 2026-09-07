"""sawc_derive service — Analogical Prompting (arXiv:2310.01714) + MPSC derived-code enrichment for thin vault blocks. Deterministic helpers live in domain.py/prompts.py (this module's own architecture note); this module is I/O (bandit, MinIO, Redis) only — previously duplicated the domain.py/prompts.py helpers locally, which is what let Optimal-Stopping's gate (body_passes_derive_gate) go unwired without anyone noticing."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState
from ..vault.domain import sentinelize_doc
from ..vault.schemas import VaultEntry

from .domain import (
    body_passes_derive_gate,
    is_thin_block,
    parse_code_block,
    python_ast_valid,
    rank_mpsc_samples,
)
from .keys import derive_latest_key, sawc_latest_key
from .params import (
    CONCURRENCY,
    DERIVE_OPTIMAL_STOPPING_ENABLED,
    ENV_ENABLED,
    MAX_DERIVES_PER_CHAPTER,
    MAX_OUTPUT_TOKENS,
    N_MPSC_SAMPLES,
    REEXPLAIN_MAX_TOKENS,
    REQUEST_TIMEOUT_S,
)
from .prompts import build_analogical_prompt, build_reexplain_prompt
from .schemas import DeriveAttempt, DeriveStats


logger = logging.getLogger(__name__)


def _env_enabled() -> bool:
    """Default ON; explicit 'false'/'0'/'no'/'off' disables via ENV_ENABLED env var."""
    raw = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return True
    return False


async def _load_referenced_vault_entries(
    minio,
    slug: str,
    needed_hashes: set[str],
    source_keys: list[str],
) -> dict[str, VaultEntry]:
    """Walk source docs, sentinelize, and return entries matching needed_hashes. Mirrors sawc_write's runtime fallback but trims to just derive-needed hashes."""
    found: dict[str, VaultEntry] = {}
    if not needed_hashes:
        return found
    for source_key in source_keys:
        if not source_key or not source_key.startswith("ingestion/"):
            continue
        try:
            raw = await minio.read_text(source_key)
            if not raw or "<code-ref hash=" in raw:
                continue
            _, entries = sentinelize_doc(raw)
            for h, entry in (entries or {}).items():
                if h in needed_hashes and h not in found:
                    found[h] = entry
            if len(found) == len(needed_hashes):
                break  # short-circuit when all hashes are covered
        except Exception as e:
            logger.debug(
                f"[sawc_derive] vault read for {source_key!r} failed: "
                f"{type(e).__name__}: {e}"
            )
            continue
    return found


async def _reexplain_one(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
) -> Optional[str]:
    """One bandit-routed call to regenerate explanation for the newly-promoted derived code. Returns new explanation string or None (caller keeps old)."""
    import json as _json

    prompt = build_reexplain_prompt(
        framework=framework,
        section_heading=section_heading,
        subheading=subheading,
        old_explanation=old_explanation,
        derived_code=derived_code,
        lang="python",
    )
    try:
        # NIM/Mistral honor response_format=json_schema/json_object
        # server-side (same pattern as sawc_write/digest_construct); a
        # bare-text ask relied on prompt instructions alone.
        response, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=REEXPLAIN_MAX_TOKENS,
            temperature=0.4,
            response_format={"type": "json_object"},
            timeout_s=REQUEST_TIMEOUT_S,
        )
    except Exception as e:
        logger.debug(
            f"[sawc_derive] re-explain call failed: {type(e).__name__}: {e}"
        )
        return None
    if not response:
        return None
    import re as _re
    m = _re.search(r"\{.*\}", response, _re.DOTALL)
    if not m:
        return None
    try:
        parsed = _json.loads(m.group())
    except Exception:
        return None
    expl = (parsed.get("explanation") or "").strip()
    if not expl:
        return None
    # Tight sanity: 6-100 words, no code fences. Pydantic's 8-80 floor
    # will reject the subtopic if we miss too far, so be permissive here.
    words = expl.split()
    if len(words) < 6 or len(words) > 100:
        return None
    if "```" in expl or "<code-ref" in expl or "<code id" in expl:
        return None
    return expl


async def _sample_one(prompt: str) -> tuple[str, Optional[str], int]:
    """Single bandit-routed LLM call. Returns (body_text, deployment, wall_ms).
    Body is empty on failure; caller decides how to count it."""
    t0 = time.monotonic()
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.7,
            timeout_s=REQUEST_TIMEOUT_S,
        )
        deployment = (meta or {}).get("deployment")
        body = parse_code_block(response or "")
        return body, deployment, int((time.monotonic() - t0) * 1000)
    except Exception as e:
        logger.debug(
            f"[sawc_derive] sample failed: {type(e).__name__}: {e}"
        )
        return "", None, int((time.monotonic() - t0) * 1000)


async def _derive_one_subtopic(
    *,
    section_id: str,
    subtopic: dict,
    original_body: str,
    framework: str,
    chapter_title: str,
    section_heading: str,
    sem: asyncio.Semaphore,
) -> DeriveAttempt:
    """Run MPSC for one subtopic. Mutates `subtopic` IN PLACE on success.
    Returns the attempt record either way. Optimal-Stopping (same pattern
    as outline_sdp/sawc_write, arXiv 2510.01394): ship sample 0 directly
    when it already clears body_passes_derive_gate, skipping the
    remaining N-1 Rotator calls. This was documented in params.py and
    domain.py (body_passes_derive_gate exists for exactly this) but never
    actually wired in — every subtopic burned all N_MPSC_SAMPLES calls
    regardless."""
    async with sem:
        original_chars = len(original_body or "")
        original_lines = (original_body or "").count("\n") + 1
        sub_meta = dict(
            section_id=section_id,
            subheading=str(subtopic.get("subheading") or ""),
            code_ref_hash=str(subtopic.get("code_ref_hash") or ""),
            original_chars=original_chars,
            original_lines=original_lines,
        )
        t0 = time.monotonic()
        prompt = build_analogical_prompt(
            framework=framework,
            chapter_title=chapter_title,
            section_heading=section_heading,
            subheading=str(subtopic.get("subheading") or ""),
            explanation=str(subtopic.get("explanation") or ""),
            original_body=original_body,
        )
        if DERIVE_OPTIMAL_STOPPING_ENABLED and N_MPSC_SAMPLES >= 2:
            r0 = await _sample_one(prompt)
            results = [r0]
            if body_passes_derive_gate(r0[0]):
                logger.info(
                    f"[sawc_derive] {section_id}/"
                    f"{sub_meta['subheading']!r}: Optimal-Stopping fired — "
                    f"sample 0 clean; skipping remaining "
                    f"{N_MPSC_SAMPLES - 1} sample(s)"
                )
            else:
                remaining = await asyncio.gather(
                    *[_sample_one(prompt) for _ in range(N_MPSC_SAMPLES - 1)],
                    return_exceptions=False,
                )
                results.extend(remaining)
        else:
            results = await asyncio.gather(
                *[_sample_one(prompt) for _ in range(N_MPSC_SAMPLES)],
                return_exceptions=False,
            )
        bodies = [r[0] for r in results]
        deployment = next((r[1] for r in results if r[1]), None)
        n_valid = sum(1 for b in bodies if b and python_ast_valid(b))
        chosen_idx, _scores = rank_mpsc_samples(bodies)
        wall_ms = int((time.monotonic() - t0) * 1000)
        if chosen_idx is None:
            decision = "rejected_ast" if n_valid == 0 else "rejected_len"
            if not any(bodies):
                decision = "rotator_fail"
            return DeriveAttempt(
                decision=decision,
                n_samples_tried=len(results),
                n_samples_valid=n_valid,
                deployment=deployment,
                wall_ms=wall_ms,
                **sub_meta,
            )
        winner = bodies[chosen_idx]
        # ── Mutate subtopic in place ────────────────────────────────────
        subtopic["code_source"] = "derived"
        subtopic["derived_code"] = winner
        # Derived code is richer than the original signature; regenerate explanation to keep prose↔code alignment (one extra bandit call per promotion).
        new_expl = await _reexplain_one(
            framework=framework,
            section_heading=section_heading,
            subheading=str(subtopic.get("subheading") or ""),
            old_explanation=str(subtopic.get("explanation") or ""),
            derived_code=winner,
        )
        if new_expl:
            subtopic["explanation"] = new_expl

        return DeriveAttempt(
            decision="promoted",
            derived_chars=len(winner),
            derived_lines=sum(1 for ln in winner.splitlines() if ln.strip()),
            n_samples_tried=len(results),
            n_samples_valid=n_valid,
            chosen_sample_idx=chosen_idx,
            deployment=deployment,
            wall_ms=wall_ms,
            **sub_meta,
        )


async def sawc_derive_run(state: SynthState) -> dict:
    """Enrich thin subtopics with AI-derived runnable examples."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "derive_stats": {
                "skipped":  "no_slug_or_chapter_id",
                "wall_ms":  0,
            },
        }

    t0 = time.monotonic()
    minio = get_storage()
    sawc_key = sawc_latest_key(slug, chapter_id)

    if not await minio.exists(sawc_key):
        # sawc_write failed or hasn't run — nothing to do.
        await emit_progress(
            thread_id, "sawc_derive", "skipped",
            chapter_id=chapter_id, reason="sawc_latest_missing",
        )
        return {
            "derive_stats": {
                "skipped": "sawc_latest_missing",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
        }

    try:
        sawc_payload = json.loads(await minio.read_text(sawc_key))
    except Exception as e:
        logger.warning(
            f"[sawc_derive] sawc-latest unreadable: {type(e).__name__}: {e}"
        )
        await emit_progress(
            thread_id, "sawc_derive", "skipped",
            chapter_id=chapter_id, reason="sawc_unreadable",
        )
        return {
            "derive_stats": {
                "skipped": "sawc_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
        }

    sections: list[dict] = sawc_payload.get("sections") or []
    chapter_title = (
        sawc_payload.get("chapter_title")
        or sawc_payload.get("chapter_id")
        or chapter_id
    )
    framework = sawc_payload.get("framework_slug") or slug

    # Total subtopics + signature-only candidates.
    n_subtopics_total = 0
    for s in sections:
        n_subtopics_total += len(s.get("subtopics") or [])

    enabled = _env_enabled()
    await emit_progress(
        thread_id, "sawc_derive", "start",
        chapter_id=chapter_id, enabled=enabled,
        n_subtopics_total=n_subtopics_total,
    )

    if not enabled:
        attempts = [
            DeriveAttempt(
                section_id=str(s.get("section_id") or ""),
                subheading=str(st.get("subheading") or ""),
                code_ref_hash=str(st.get("code_ref_hash") or ""),
                original_chars=0,
                original_lines=0,
                decision="disabled",
            )
            for s in sections for st in (s.get("subtopics") or [])
        ]
        stats = DeriveStats(
            chapter_id=chapter_id,
            framework_slug=framework,
            enabled=False,
            n_subtopics_total=n_subtopics_total,
            n_candidates_thin=0,
            n_promoted=0,
            n_rejected_ast=0,
            n_rejected_len=0,
            n_rotator_fail=0,
            wall_ms=int((time.monotonic() - t0) * 1000),
            attempts=attempts,
        )
        await minio.write(
            derive_latest_key(slug, chapter_id),
            json.dumps(stats.model_dump(), indent=2),
            content_type="application/json",
        )
        await emit_progress(
            thread_id, "sawc_derive", "done",
            chapter_id=chapter_id, **{
                k: v for k, v in stats.model_dump(exclude={"attempts"}).items()
            },
        )
        return {"derive_stats": stats.model_dump()}

    # ── Locate thin candidates ─────────────────────────────────────────
    needed_hashes: set[str] = set()
    candidates: list[tuple[dict, dict, str]] = []  # (section, subtopic, expected_hash)
    for s in sections:
        sub_list = s.get("subtopics") or []
        for st in sub_list:
            if not isinstance(st, dict):
                continue
            if st.get("code_source") == "derived":
                continue  # already derived
            h = st.get("code_ref_hash") or ""
            if h:
                needed_hashes.add(h)
                candidates.append((s, st, h))

    # Pull vault entries for those hashes.
    per_source = sawc_payload.get("per_source") or []
    if not per_source:
        # sawc blob doesn't carry per_source; reconstruct from citations.
        cite_sources: set[str] = set()
        for s in sections:
            for c in (s.get("citations") or []):
                k = c.get("source_key") or ""
                if k:
                    cite_sources.add(k)
        source_keys = sorted(cite_sources)
    else:
        source_keys = sorted({x.get("source_key") for x in per_source if x})

    vault = await _load_referenced_vault_entries(
        minio, slug, needed_hashes, source_keys,
    )

    thin_candidates: list[tuple[dict, dict, str, str]] = []  # +original_body
    for sec, st, h in candidates:
        entry = vault.get(h)
        body = ""
        if entry is not None:
            body = entry.fence_text or ""
        if body and is_thin_block(body):
            thin_candidates.append((sec, st, h, body))
    # Cap by burst protection.
    if len(thin_candidates) > MAX_DERIVES_PER_CHAPTER:
        thin_candidates = thin_candidates[:MAX_DERIVES_PER_CHAPTER]

    n_candidates_thin = len(thin_candidates)
    await emit_progress(
        thread_id, "sawc_derive", "candidates_identified",
        n_candidates_thin=n_candidates_thin,
        n_subtopics_total=n_subtopics_total,
        vault_entries_loaded=len(vault),
    )

    # ── Fan out MPSC sampling ─────────────────────────────────────────
    sem = asyncio.Semaphore(CONCURRENCY)
    derive_tasks = [
        _derive_one_subtopic(
            section_id=str(sec.get("section_id") or ""),
            subtopic=st,
            original_body=body,
            framework=framework,
            chapter_title=chapter_title,
            section_heading=str(sec.get("heading") or ""),
            sem=sem,
        )
        for (sec, st, h, body) in thin_candidates
    ]
    if derive_tasks:
        results: list[DeriveAttempt] = await asyncio.gather(*derive_tasks)
    else:
        results = []

    # Account for non-thin subtopics so the attempts log is complete.
    thin_set = {id(st) for _, st, _, _ in thin_candidates}
    skipped_attempts: list[DeriveAttempt] = []
    for sec, st, h in candidates:
        if id(st) in thin_set:
            continue
        body = ""
        entry = vault.get(h)
        if entry is not None:
            body = entry.fence_text or ""
        skipped_attempts.append(DeriveAttempt(
            section_id=str(sec.get("section_id") or ""),
            subheading=str(st.get("subheading") or ""),
            code_ref_hash=h,
            original_chars=len(body),
            original_lines=(body.count("\n") + 1) if body else 0,
            decision="skipped_thin",
        ))

    all_attempts = list(results) + skipped_attempts

    n_promoted = sum(1 for a in results if a.decision == "promoted")
    n_rejected_ast = sum(1 for a in results if a.decision == "rejected_ast")
    n_rejected_len = sum(1 for a in results if a.decision == "rejected_len")
    n_rotator_fail = sum(1 for a in results if a.decision == "rotator_fail")

    # ── Mutate sawc-latest.json in place if any derives promoted ─────
    if n_promoted > 0:
        try:
            await minio.write(
                sawc_key,
                json.dumps(sawc_payload, indent=2, ensure_ascii=False),
                content_type="application/json",
            )
        except Exception as e:
            logger.error(
                f"[sawc_derive] failed to persist mutated sawc-latest: "
                f"{type(e).__name__}: {e}"
            )

    stats = DeriveStats(
        chapter_id=chapter_id,
        framework_slug=framework,
        enabled=True,
        n_subtopics_total=n_subtopics_total,
        n_candidates_thin=n_candidates_thin,
        n_promoted=n_promoted,
        n_rejected_ast=n_rejected_ast,
        n_rejected_len=n_rejected_len,
        n_rotator_fail=n_rotator_fail,
        wall_ms=int((time.monotonic() - t0) * 1000),
        attempts=all_attempts,
    )

    try:
        await minio.write(
            derive_latest_key(slug, chapter_id),
            json.dumps(stats.model_dump(), indent=2),
            content_type="application/json",
        )
    except Exception as e:
        logger.warning(
            f"[sawc_derive] failed to persist derive-latest: "
            f"{type(e).__name__}: {e}"
        )

    await emit_progress(
        thread_id, "sawc_derive", "done",
        chapter_id=chapter_id,
        n_subtopics_total=n_subtopics_total,
        n_candidates_thin=n_candidates_thin,
        n_promoted=n_promoted,
        n_rejected_ast=n_rejected_ast,
        n_rejected_len=n_rejected_len,
        n_rotator_fail=n_rotator_fail,
        wall_ms=stats.wall_ms,
    )

    logger.info(
        f"[sawc_derive] {slug}/{chapter_id}: "
        f"{n_promoted}/{n_candidates_thin} promoted "
        f"(ast_fail={n_rejected_ast}, len_fail={n_rejected_len}, "
        f"rotator_fail={n_rotator_fail}); {stats.wall_ms} ms"
    )

    return {"derive_stats": stats.model_dump()}
