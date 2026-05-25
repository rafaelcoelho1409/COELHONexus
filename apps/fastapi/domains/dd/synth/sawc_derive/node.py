"""sawc_derive — graph node (Ship #95, 2026-05-24).

Inserted AFTER sawc_write commits its chapter blob and BEFORE
checklist_eval. Scans the just-written sections for subtopics whose
vault entry is signature-only / too thin to teach effectively, then
fires Analogical-Prompting + MPSC (Multi-Path Self-Consistency, arXiv
2503.04611) to generate runnable derived examples. Successful derives
are written back onto the Subtopic (`code_source='derived'` +
`derived_code=<body>`), and the sawc-latest.json blob is mutated in
place so checklist and render naturally see the enriched chapter.

NON-DESTRUCTIVE WITH OP-12 CAVEAT
  When CoRefine loopback fires and a later iter scores LOWER than a
  prior iter, render's OP-12 rescue reads from `best_seen_sawc_path`
  — a VERSIONED blob written by sawc_write itself (pre-derive). The
  rescued chapter therefore lacks derive enhancements. This is an
  accepted tradeoff: the rescued chapter still passes its own audit
  (it scored ≥ the loser anyway); derives are a pedagogical bonus,
  not a correctness gate.

ENV FLAG
  KD_ENABLE_SAWC_DERIVE=false → node still runs (emits start/done,
  records a `disabled` attempt per subtopic) but skips all LLM calls.
  Default ON.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ...ingestion.storage import get_storage
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import SynthState
from ..vault.service import sentinelize_doc as _sentinelize_doc
from ..vault.types import VaultEntry
from .constants import (
    _CONCURRENCY,
    _DD_PROCESS,
    _ENV_ENABLED,
    _MAX_DERIVES_PER_CHAPTER,
    _MAX_OUTPUT_TOKENS,
    _N_MPSC_SAMPLES,
    SAWC_DERIVE_PROMPT_VERSION,
    SAWC_DERIVE_SCHEMA_VERSION,
)
from .service import (
    build_analogical_prompt,
    build_reexplain_prompt,
    is_thin_block,
    parse_code_block,
    python_ast_valid,
    rank_mpsc_samples,
)
from .types import DeriveAttempt, DeriveStats


logger = logging.getLogger(__name__)


_BLOB_PREFIX = "synth"


def _sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def _derive_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc_derive-latest.json"


def _ingestion_source_key(slug: str, basename: str) -> str:
    # Mirrors the pattern the rest of synth uses to read raw ingestion pages.
    return f"ingestion/{slug}/pages/{basename}"


def _env_enabled() -> bool:
    """Default ON; explicit 'false' / '0' / 'no' disables."""
    raw = (os.environ.get(_ENV_ENABLED) or "").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return True
    return False


# =============================================================================
# Vault lookup — minimal version (we only need fence_text for hashes that
# the chapter already references; no need to load every per-source vault).
# =============================================================================
async def _load_referenced_vault_entries(
    minio,
    slug: str,
    needed_hashes: set[str],
    source_keys: list[str],
) -> dict[str, VaultEntry]:
    """Walk the chapter's per_source list, sentinelize each raw doc, and
    return only the entries whose hash is in `needed_hashes`. Mirrors
    sawc_write's runtime fallback but trims to just what derive needs.
    """
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
            _, entries = _sentinelize_doc(raw)
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


# =============================================================================
# MPSC sampling — N parallel LLM calls
# =============================================================================
async def _reexplain_one(
    *,
    framework: str,
    section_heading: str,
    subheading: str,
    old_explanation: str,
    derived_code: str,
) -> Optional[str]:
    """Fire ONE bandit-routed call to regenerate the explanation
    against the newly-promoted derived_code. Returns the new explanation
    string or None on any failure (caller keeps old explanation).
    """
    import json as _json
    from .constants import _DD_PROCESS_REEXPLAIN, _REEXPLAIN_MAX_TOKENS

    prompt = build_reexplain_prompt(
        framework=framework,
        section_heading=section_heading,
        subheading=subheading,
        old_explanation=old_explanation,
        derived_code=derived_code,
        lang="python",
    )
    try:
        response, _meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_REEXPLAIN_MAX_TOKENS,
            temperature=0.4,
            dd_process=_DD_PROCESS_REEXPLAIN,
        )
    except Exception as e:
        logger.debug(
            f"[sawc_derive] re-explain call failed: {type(e).__name__}: {e}"
        )
        return None
    if not response:
        return None
    # Extract JSON; tolerate prose preamble.
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
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.7,
            dd_process=_DD_PROCESS,
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
    Returns the attempt record either way."""
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
        results = await asyncio.gather(
            *[_sample_one(prompt) for _ in range(_N_MPSC_SAMPLES)],
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
                n_samples_tried=_N_MPSC_SAMPLES,
                n_samples_valid=n_valid,
                deployment=deployment,
                wall_ms=wall_ms,
                **sub_meta,
            )
        winner = bodies[chosen_idx]
        # ── Mutate subtopic in place ────────────────────────────────────
        subtopic["code_source"] = "derived"
        subtopic["derived_code"] = winner

        # ── Ship D (2026-05-25): re-explain ─────────────────────────────
        # The original explanation was written for the thin signature;
        # the derived code is a different (richer) example. Regenerate
        # the explanation conditioned on the new code body so prose↔code
        # alignment holds. One extra bandit call per promotion.
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
            n_samples_tried=_N_MPSC_SAMPLES,
            n_samples_valid=n_valid,
            chosen_sample_idx=chosen_idx,
            deployment=deployment,
            wall_ms=wall_ms,
            **sub_meta,
        )


# =============================================================================
# Graph node entrypoint
# =============================================================================
@traced("sawc_derive")
async def sawc_derive(state: SynthState) -> dict:
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
    sawc_key = _sawc_latest_key(slug, chapter_id)

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
            _derive_latest_key(slug, chapter_id),
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

    # Filter to actually-thin candidates.
    thin_candidates: list[tuple[dict, dict, str, str]] = []  # +original_body
    for sec, st, h in candidates:
        entry = vault.get(h)
        body = ""
        if entry is not None:
            body = entry.fence_text or ""
        if body and is_thin_block(body):
            thin_candidates.append((sec, st, h, body))
    # Cap by burst protection.
    if len(thin_candidates) > _MAX_DERIVES_PER_CHAPTER:
        thin_candidates = thin_candidates[:_MAX_DERIVES_PER_CHAPTER]

    n_candidates_thin = len(thin_candidates)
    await emit_progress(
        thread_id, "sawc_derive", "candidates_identified",
        n_candidates_thin=n_candidates_thin,
        n_subtopics_total=n_subtopics_total,
        vault_entries_loaded=len(vault),
    )

    # ── Fan out MPSC sampling ─────────────────────────────────────────
    sem = asyncio.Semaphore(_CONCURRENCY)
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
            _derive_latest_key(slug, chapter_id),
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
