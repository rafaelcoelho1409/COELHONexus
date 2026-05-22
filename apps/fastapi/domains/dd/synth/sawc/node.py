"""sawc_write — Structure-Aware Writing Controller (SurveyGen-I + MAMM).

Step 6 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the sawc_write deep
research report). The third LLM-driven synth graph node, runs after
digest_construct commits its checkpoint.

WHAT IT DOES (per chapter):

  1. Loads outline-latest.json (from outline_sdp) + digest-latest.json
     (from digest_construct). The outline carries the DAG stages; the
     digest carries the per_section index telling the writer what each
     section should cover.
  2. Iterates DAG stages SEQUENTIALLY (stage 0 → stage 1 → ...). Within
     each stage, sections write CONCURRENTLY (bounded by `_CONCURRENCY`).
     This is the SurveyGen-I §3.2 stage-parallel algorithm.
  3. For EACH section, runs MAMM-Refine multi-agent best-of-N:
       - Fire N=3 writer drafts in parallel (3 distinct rotator picks)
       - 1 critic-picker call from a DIFFERENT model family
       - Picker chooses by structural rubric; falls back to deterministic
         structural scoring (Self-Certainty proxy) if critic LLM fails
  4. After each stage completes, derives MemoryEntry per section
     DETERMINISTICALLY (no extra LLM call — pulls terminology from
     digest contributions). The accumulated memory ledger is passed
     to the NEXT stage's sections so they have cross-section context.
  5. Persists ChapterDraft to MinIO (versioned + latest pointer).
  6. Returns state patch with `sawc_path` + `sawc_stats`.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/sawc/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/sawc-latest.json

  Manifest hash includes:
    outline_manifest_hash
    digest_manifest_hash
    prompt_version
    schema_version

  Cache hit returns immediately + emits `done` SSE with cache_hit=true.

FAIL-SOFT BEHAVIOR (matches outline_sdp / digest_construct patterns):

  - One draft's LLM call fails: log + emit section_draft_done(ok=false),
    keep going with the remaining drafts. Picker chooses from the
    successful ones.
  - All 3 drafts fail: emit a placeholder Section + flag in `issues`.
    mgsr_replan will re-target this section for retry.
  - Critic LLM returns malformed JSON / wrong index: fall back to
    structural scoring (Self-Certainty proxy) over the same candidates.
  - Pydantic validation fails on a draft: run repair LLM call with the
    validation errors as feedback. Max 2 repair attempts.

SSE EVENTS — real-time UI mechanism (per the established pattern):

  start              chapter_id, chapter_title, n_stages, n_sections,
                      n_total_drafts (= 3 × n_sections)
  stage_start        stage_idx, n_sections_in_stage, section_ids
  section_draft_done section_id, draft_idx, n_total (3), ok, wall_ms,
                      deployment, error?, n_paragraphs?
  section_picked     section_id, chosen_idx, n_violations, fallback?,
                      structural_score, deployment_critic
  section_done       section_id, n_paragraphs, n_code_refs, n_citations,
                      total_chars, n_repairs, wall_ms
  stage_done         stage_idx, n_completed, n_failed, wall_ms
  done               n_sections, n_completed, n_fallback, n_repairs,
                      total_drafts_fired, wall_ms, cache_hit
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from hashlib import sha256
from typing import Optional

from pydantic import ValidationError

from ...ingestion.storage import get_storage
from domains.llm.rotator.chain import chat_judge_bandit_async

from ..observability.spans import traced
from ..progress import emit_progress
from .constants import (
    SAWC_PROMPT_VERSION,
    SAWC_SCHEMA_VERSION,
)
from .service import (
    build_critic_picker_prompt,
    build_repair_prompt,
    build_writer_prompt,
    compute_sawc_stats,
    extract_memory_entry,
    score_draft_structural,
    summarize_candidate,
    validate_section_against_inputs,
)
from .types import (
    ChapterDraft,
    Citation,
    CodeRef,
    MemoryEntry,
    SAWCStats,
    Section,
    _LLMSectionDraft,
)
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables (quality > speed per project memory feedback_kd_quality_over_speed)
# =============================================================================
_N_DRAFTS              = 3       # MAMM-Refine recipe
_CONCURRENCY           = 6       # max concurrent SECTIONS per stage
_TEMPERATURE_DRAFT     = 0.5     # variety across drafts (MAMM diversity)
_TEMPERATURE_CRITIC    = 0.0
_TEMPERATURE_REPAIR    = 0.2
_MAX_TOKENS_DRAFT      = 8000
_MAX_TOKENS_CRITIC     = 300
_MAX_TOKENS_REPAIR     = 8000
_MAX_REPAIR_ATTEMPTS   = 2

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc/{manifest_hash}.json"


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def _outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


def _digest_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/digest-latest.json"


# =============================================================================
# JSON helpers
# =============================================================================
def _parse_json_response(text: str) -> Optional[dict]:
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


def _shorten_pydantic_error(e: ValidationError) -> str:
    errs = e.errors()
    if not errs:
        return "Pydantic validation failed (no detail)"
    lines = []
    for err in errs[:4]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 4} more)" if len(errs) > 4 else ""
    return "; ".join(lines) + suffix


def _try_parse_draft(
    raw: dict,
) -> tuple[Optional[_LLMSectionDraft], Optional[str]]:
    try:
        return _LLMSectionDraft.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


# =============================================================================
# Per-draft pipeline
# =============================================================================
async def _draft_one_section(
    *,
    draft_idx: int,
    n_total: int,
    thread_id: str,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
) -> tuple[Optional[_LLMSectionDraft], Optional[str], int, int]:
    """One writer call → parse → Pydantic → cross-ref → repair.

    Returns (draft, deployment, wall_ms, n_repairs). draft is None
    on irrecoverable failure.

    Emits ONE `section_draft_done` event so the UI shows progress
    through the N=3 fan-out (real-time mechanism we established for
    outline_sdp + digest_construct)."""
    t0 = time.monotonic()
    allowed_hash_set = set(allowed_hashes)
    valid_source_set = set(valid_source_keys)

    prompt = build_writer_prompt(
        framework=framework,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        section_id=section_id,
        section_heading=section_heading,
        section_description=section_description,
        section_prerequisites=section_prerequisites,
        contributions=contributions,
        allowed_hashes=allowed_hashes,
        valid_source_keys=valid_source_keys,
        memory=memory,
        n_primary_contribs=n_primary_contribs,
    )

    deployment: Optional[str] = None
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_DRAFT,
            temperature=_TEMPERATURE_DRAFT,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"{type(e).__name__}: {str(e)[:120]}",
            wall_ms=wall_ms,
        )
        return None, None, wall_ms, 0

    parsed = _parse_json_response(response)
    if not parsed:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error="parse_failed", wall_ms=wall_ms,
            deployment=deployment,
        )
        return None, deployment, wall_ms, 0

    draft, err = _try_parse_draft(parsed)
    n_repairs = 0
    current = parsed

    # Pydantic-fail repair loop
    while draft is None and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        issues = [f"Pydantic schema rejected the previous output: {err}"]
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(current, indent=2),
            issues=issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp:
                current = rp
                draft, err = _try_parse_draft(rp)
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: repair "
                f"attempt {n_repairs} failed: {type(e).__name__}: {e}"
            )
            break

    if draft is None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "section_draft_done",
            section_id=section_id, draft_idx=draft_idx, n_total=n_total,
            ok=False, error=f"pydantic_fail: {err}",
            wall_ms=wall_ms, deployment=deployment,
        )
        return None, deployment, wall_ms, n_repairs

    # Cross-ref validation (heading/hashes/citations)
    issues = validate_section_against_inputs(
        draft,
        expected_heading=section_heading,
        allowed_hashes=allowed_hash_set,
        valid_source_keys=valid_source_set,
    )
    # Repair if issues
    while issues and n_repairs < _MAX_REPAIR_ATTEMPTS:
        n_repairs += 1
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            section_id=section_id,
            section_heading=section_heading,
            section_description=section_description,
            section_prerequisites=section_prerequisites,
            contributions=contributions,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            memory=memory,
            current_json=json.dumps(draft.model_dump(), indent=2),
            issues=issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if not rp:
                break
            new_draft, new_err = _try_parse_draft(rp)
            if new_draft is None:
                break
            new_issues = validate_section_against_inputs(
                new_draft,
                expected_heading=section_heading,
                allowed_hashes=allowed_hash_set,
                valid_source_keys=valid_source_set,
            )
            # Accept ONLY if it strictly reduces violation count
            if len(new_issues) < len(issues):
                draft = new_draft
                issues = new_issues
            else:
                break
        except Exception as e:
            logger.warning(
                f"[sawc_write] {section_id} draft {draft_idx}: cross-ref "
                f"repair attempt {n_repairs} failed: "
                f"{type(e).__name__}: {e}"
            )
            break

    wall_ms = int((time.monotonic() - t0) * 1000)
    await emit_progress(
        thread_id, "sawc_write", "section_draft_done",
        section_id=section_id, draft_idx=draft_idx, n_total=n_total,
        ok=True, wall_ms=wall_ms, deployment=deployment,
        n_paragraphs=len(draft.paragraphs),
        n_code_refs=len(draft.code_refs),
        n_citations=len(draft.citations),
        n_violations=len(issues),
    )
    return draft, deployment, wall_ms, n_repairs


# =============================================================================
# Critic picker — MAMM-Refine rerank step
# =============================================================================
async def _critic_pick_best(
    *,
    section_id: str,
    section_heading: str,
    n_primary_contribs: int,
    candidates: list[_LLMSectionDraft],
    expected_heading: str,
    allowed_hashes: set[str],
    valid_source_keys: set[str],
    thread_id: str,
) -> tuple[int, Optional[str], Optional[str], float]:
    """Return (chosen_idx, deployment_critic, fallback_used, structural_score).

    fallback_used ∈ {None, "structural_score"} — None means the critic
    LLM picked; "structural_score" means we fell back to deterministic
    scoring.
    """
    summaries = [
        summarize_candidate(
            c,
            expected_heading=expected_heading,
            allowed_hashes=allowed_hashes,
            valid_source_keys=valid_source_keys,
            n_primary_contribs=n_primary_contribs,
        )
        for c in candidates
    ]

    if len(candidates) <= 1:
        score = summaries[0]["structural_score"] if summaries else 0.0
        return 0, None, None, score

    prompt = build_critic_picker_prompt(
        section_id=section_id,
        section_heading=section_heading,
        n_primary_contribs=n_primary_contribs,
        candidates_summary=summaries,
    )
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_CRITIC,
            temperature=_TEMPERATURE_CRITIC,
        )
        deployment_critic = (meta or {}).get("deployment")
        parsed = _parse_json_response(response)
        if parsed and "chosen_index" in parsed:
            idx = int(parsed["chosen_index"])
            if 0 <= idx < len(candidates):
                return (
                    idx,
                    deployment_critic,
                    None,
                    summaries[idx]["structural_score"],
                )
    except Exception as e:
        logger.warning(
            f"[sawc_write] {section_id}: critic LLM failed: "
            f"{type(e).__name__}: {e} — falling back to structural score"
        )

    # Fallback: deterministic argmax by structural score (Self-Certainty
    # proxy; arXiv 2502.18581)
    scores = [s["structural_score"] for s in summaries]
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    return best_idx, None, "structural_score", scores[best_idx]


# =============================================================================
# Placeholder section (when ALL drafts fail)
# =============================================================================
def _placeholder_section(
    *,
    section_id: str,
    heading: str,
    n_repairs: int,
    deployment_writer: Optional[str],
) -> Section:
    """Returned when every writer draft + every repair attempt fails.
    Keeps the chapter assemblable and surfaces the failure to
    mgsr_replan via `issues`."""
    return Section(
        section_id=section_id,
        heading=heading,
        paragraphs=[
            f"This section ({heading}) is awaiting content. The synth "
            f"writer was unable to produce a valid draft on its initial "
            f"pass; mgsr_replan should retarget this section or merge "
            f"it into an adjacent section in the next iteration.",
            f"Placeholder added by sawc_write to preserve chapter "
            f"structure for downstream nodes (checklist_eval, "
            f"render_audit_write).",
        ],
        code_refs=[],
        citations=[],
        n_drafts_tried=_N_DRAFTS,
        n_repairs=n_repairs,
        deployment_writer=deployment_writer,
        issues=["placeholder"],
    )


# =============================================================================
# Per-section pipeline (best-of-N + critic pick)
# =============================================================================
async def _write_section_best_of_n(
    *,
    sem: asyncio.Semaphore,
    section_id: str,
    section_heading: str,
    section_description: str,
    section_prerequisites: list[str],
    contributions: list[dict],
    allowed_hashes: list[str],
    valid_source_keys: list[str],
    memory: list[dict],
    n_primary_contribs: int,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    thread_id: str,
) -> Section:
    """Full per-section pipeline: N drafts → critic-pick → Section."""
    async with sem:
        t0 = time.monotonic()

        # Fire N=_N_DRAFTS writer calls in parallel
        draft_tasks = [
            _draft_one_section(
                draft_idx=i,
                n_total=_N_DRAFTS,
                thread_id=thread_id,
                framework=framework,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                section_id=section_id,
                section_heading=section_heading,
                section_description=section_description,
                section_prerequisites=section_prerequisites,
                contributions=contributions,
                allowed_hashes=allowed_hashes,
                valid_source_keys=valid_source_keys,
                memory=memory,
                n_primary_contribs=n_primary_contribs,
            )
            for i in range(_N_DRAFTS)
        ]
        results = await asyncio.gather(*draft_tasks)

        # Filter to drafts that parsed + validated
        valid: list[tuple[int, _LLMSectionDraft, str, int, int]] = []
        for i, (draft, dep, wall, repairs) in enumerate(results):
            if draft is not None:
                valid.append((i, draft, dep or "", wall, repairs))

        if not valid:
            # ALL drafts failed → placeholder
            await emit_progress(
                thread_id, "sawc_write", "section_picked",
                section_id=section_id, chosen_idx=-1,
                n_violations=0, fallback="all_drafts_failed",
                structural_score=0.0,
            )
            await emit_progress(
                thread_id, "sawc_write", "section_done",
                section_id=section_id, n_paragraphs=2,
                n_code_refs=0, n_citations=0, total_chars=0,
                n_repairs=sum(r[3] for r in results),
                wall_ms=int((time.monotonic() - t0) * 1000),
                fallback="placeholder",
            )
            return _placeholder_section(
                section_id=section_id,
                heading=section_heading,
                n_repairs=sum(r[3] for r in results),
                deployment_writer=(
                    next((d for _, _, d, _, _ in valid), None)
                    if valid else None
                ),
            )

        # Critic picker over valid drafts (rerank, not regenerate)
        chosen_idx, dep_critic, fallback, structural_score = (
            await _critic_pick_best(
                section_id=section_id,
                section_heading=section_heading,
                n_primary_contribs=n_primary_contribs,
                candidates=[d for _, d, _, _, _ in valid],
                expected_heading=section_heading,
                allowed_hashes=set(allowed_hashes),
                valid_source_keys=set(valid_source_keys),
                thread_id=thread_id,
            )
        )

        # Map picker index → original draft index (for transparency)
        original_draft_idx = valid[chosen_idx][0]
        chosen_draft = valid[chosen_idx][1]
        dep_writer = valid[chosen_idx][2]
        chosen_repairs = valid[chosen_idx][4]

        # Re-validate the chosen draft so `issues` is accurate (in case
        # the picker chose one with remaining violations after repair
        # exhaustion)
        chosen_issues = validate_section_against_inputs(
            chosen_draft,
            expected_heading=section_heading,
            allowed_hashes=set(allowed_hashes),
            valid_source_keys=set(valid_source_keys),
        )

        await emit_progress(
            thread_id, "sawc_write", "section_picked",
            section_id=section_id,
            chosen_idx=original_draft_idx,
            n_violations=len(chosen_issues),
            fallback=fallback,
            structural_score=structural_score,
            deployment_critic=dep_critic,
        )

        section = Section(
            section_id=section_id,
            heading=chosen_draft.heading,
            paragraphs=chosen_draft.paragraphs,
            code_refs=chosen_draft.code_refs,
            citations=chosen_draft.citations,
            wall_ms=int((time.monotonic() - t0) * 1000),
            deployment_writer=dep_writer,
            deployment_critic=dep_critic,
            n_drafts_tried=_N_DRAFTS,
            n_repairs=chosen_repairs,
            chosen_draft_idx=original_draft_idx,
            structural_score=structural_score,
            fallback_picker=fallback,
            issues=chosen_issues,
        )

        total_chars = sum(len(p) for p in section.paragraphs)
        await emit_progress(
            thread_id, "sawc_write", "section_done",
            section_id=section_id,
            n_paragraphs=len(section.paragraphs),
            n_code_refs=len(section.code_refs),
            n_citations=len(section.citations),
            total_chars=total_chars,
            n_repairs=chosen_repairs,
            wall_ms=section.wall_ms,
        )
        return section


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    outline_manifest_hash: str,
    digest_manifest_hash: str,
) -> str:
    payload = (
        f"outline={outline_manifest_hash}|"
        f"digest={digest_manifest_hash}|"
        f"prompt={SAWC_PROMPT_VERSION}|"
        f"schema={SAWC_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The node
# =============================================================================
@traced("sawc_write")
async def sawc_write(state: SynthState) -> dict:
    """Run the Structure-Aware Writing Controller for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "sawc_path":  "",
            "sawc_stats": {"skipped": "no_slug_or_chapter_id", "wall_ms": 0},
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # ── Load outline + digest blobs ────────────────────────────────────
    outline_key = _outline_latest_key(slug, chapter_id)
    digest_key = _digest_latest_key(slug, chapter_id)

    if not await minio.exists(outline_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":     "outline_not_found",
                "outline_key": outline_key,
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline {outline_key!r} not in MinIO — run outline_sdp first",
        }
    if not await minio.exists(digest_key):
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "digest_not_found",
                "digest_key": digest_key,
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"digest {digest_key!r} not in MinIO — run digest_construct first",
        }

    try:
        outline_text = await minio.read_text(outline_key)
        outline_payload = json.loads(outline_text)
        digest_text = await minio.read_text(digest_key)
        digest_payload = json.loads(digest_text)
    except Exception as e:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline/digest unreadable: {type(e).__name__}: {e}",
        }

    outline_data = outline_payload.get("outline") or {}
    outline_sections = outline_data.get("sections") or []
    challenges = outline_data.get("challenges") or []
    flashcards = outline_data.get("flashcards") or []
    dag = outline_payload.get("dag") or {}
    stages_raw = dag.get("stages") or {}
    chapter_title = outline_payload.get("chapter_title") or chapter_id
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    per_section_index: dict[str, list[dict]] = (
        digest_payload.get("per_section") or {}
    )
    per_source_list: list[dict] = digest_payload.get("per_source") or []
    valid_source_keys: list[str] = sorted({
        s.get("source_key", "") for s in per_source_list
        if s.get("source_key")
    })
    digest_manifest_hash = digest_payload.get("digest_manifest_hash") or ""

    if not outline_sections or not stages_raw:
        return {
            "sawc_path":  "",
            "sawc_stats": {
                "skipped":    "empty_outline_or_stages",
                "n_sections": len(outline_sections),
                "n_stages":   len(stages_raw),
                "wall_ms":    int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline has {len(outline_sections)} sections, dag "
                      f"has {len(stages_raw)} stages — both must be >0",
        }

    # Build section_id → outline_section lookup
    sections_by_id: dict[str, dict] = {
        s["section_id"]: s for s in outline_sections
    }
    # Normalize stage keys to int and sort
    stages: dict[int, list[str]] = {
        int(k): list(v) for k, v in stages_raw.items()
    }
    sorted_stage_indices = sorted(stages.keys())
    n_sections = len(outline_sections)
    n_stages = len(sorted_stage_indices)

    await emit_progress(
        thread_id, "sawc_write", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        n_stages=n_stages,
        n_sections=n_sections,
        n_total_drafts=n_sections * _N_DRAFTS,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    manifest_hash = _compute_manifest_hash(
        outline_manifest_hash=outline_manifest_hash,
        digest_manifest_hash=digest_manifest_hash,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            cov = (cached or {}).get("coverage_stats") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "n_sections":      cov.get("n_sections", 0),
                "n_completed":     cov.get("n_sections_completed", 0),
                "n_fallback":      cov.get("n_sections_fallback", 0),
                "n_repairs":       cov.get("n_repairs", 0),
                "n_stages":        cov.get("n_stages", 0),
                "n_total_drafts_fired": cov.get("n_total_drafts_fired", 0),
                "n_picker_fallbacks":   cov.get("n_picker_fallbacks", 0),
                "wall_ms":         elapsed,
                "store_path":      latest_key,
                "versioned_path":  versioned_key,
                "manifest_hash":   manifest_hash,
                "cache_hit":       True,
                "prompt_version":  cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "sawc_write", "done",
                n_sections=stats["n_sections"],
                n_completed=stats["n_completed"],
                n_fallback=stats["n_fallback"],
                n_repairs=stats["n_repairs"],
                total_drafts_fired=stats["n_total_drafts_fired"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[sawc_write] {slug}/{chapter_id}: CACHE HIT — "
                f"{stats['n_completed']}/{stats['n_sections']} sections, "
                f"{stats['n_repairs']} repairs, {elapsed} ms"
            )
            return {"sawc_path": latest_key, "sawc_stats": stats}
        except Exception as e:
            logger.warning(
                f"[sawc_write] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # ── Stage loop (sequential across stages, parallel within) ─────────
    sem = asyncio.Semaphore(_CONCURRENCY)
    memory_ledger: list[MemoryEntry] = []
    completed_sections: dict[str, Section] = {}
    n_total_drafts_fired = 0
    n_critic_picks = 0
    n_picker_fallbacks = 0

    for stage_idx in sorted_stage_indices:
        stage_section_ids = stages[stage_idx]
        stage_t0 = time.monotonic()
        await emit_progress(
            thread_id, "sawc_write", "stage_start",
            stage_idx=stage_idx,
            n_sections_in_stage=len(stage_section_ids),
            section_ids=stage_section_ids,
        )

        # Freeze memory snapshot for this stage — all sections at this
        # stage see the SAME memory (per SurveyGen-I §3.2.2: memory
        # accumulates BETWEEN stages, not within)
        memory_snapshot = [m.model_dump() for m in memory_ledger]

        async def _run_section(sid: str) -> Section:
            outline_sec = sections_by_id.get(sid)
            if not outline_sec:
                logger.warning(
                    f"[sawc_write] section_id {sid!r} in stages but not in "
                    f"outline.sections — emitting placeholder"
                )
                return _placeholder_section(
                    section_id=sid,
                    heading=sid,
                    n_repairs=0,
                    deployment_writer=None,
                )
            contributions = per_section_index.get(sid) or []
            # Allowed hashes = union of code_refs across all this section's
            # contributions (digest already gave us LLM-grounded routing)
            allowed_hashes_set: set[str] = set()
            for c in contributions:
                for h in (c.get("code_refs") or []):
                    allowed_hashes_set.add(h)
            allowed_hashes = sorted(allowed_hashes_set)
            n_primary_contribs = sum(
                1 for c in contributions if c.get("relevance") == "primary"
            )
            return await _write_section_best_of_n(
                sem=sem,
                section_id=sid,
                section_heading=outline_sec.get("heading") or sid,
                section_description=outline_sec.get("description") or "",
                section_prerequisites=(
                    outline_sec.get("prerequisites") or []
                ),
                contributions=contributions,
                allowed_hashes=allowed_hashes,
                valid_source_keys=valid_source_keys,
                memory=memory_snapshot,
                n_primary_contribs=n_primary_contribs,
                framework=slug,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                thread_id=thread_id,
            )

        section_results = await asyncio.gather(
            *(_run_section(sid) for sid in stage_section_ids),
            return_exceptions=True,
        )

        n_stage_completed = 0
        n_stage_failed = 0
        for sid, result in zip(stage_section_ids, section_results):
            if isinstance(result, BaseException):
                logger.warning(
                    f"[sawc_write] {sid}: gather raised "
                    f"{type(result).__name__}: {result} — emitting placeholder"
                )
                completed_sections[sid] = _placeholder_section(
                    section_id=sid,
                    heading=sections_by_id.get(sid, {}).get("heading", sid),
                    n_repairs=0,
                    deployment_writer=None,
                )
                n_stage_failed += 1
            else:
                completed_sections[sid] = result
                # All non-placeholder sections count toward drafts fired
                n_total_drafts_fired += _N_DRAFTS
                n_critic_picks += 1
                if result.fallback_picker == "structural_score":
                    n_picker_fallbacks += 1
                if "placeholder" in result.issues:
                    n_stage_failed += 1
                else:
                    n_stage_completed += 1

            # Accumulate memory entry deterministically
            sec = completed_sections[sid]
            contribs = per_section_index.get(sid) or []
            try:
                memory_ledger.append(extract_memory_entry(
                    sec,
                    section_contributions=contribs,
                    section_heading=sec.heading,
                ))
            except Exception as e:
                logger.warning(
                    f"[sawc_write] memory extract failed for {sid}: "
                    f"{type(e).__name__}: {e}"
                )

        stage_ms = int((time.monotonic() - stage_t0) * 1000)
        await emit_progress(
            thread_id, "sawc_write", "stage_done",
            stage_idx=stage_idx,
            n_completed=n_stage_completed,
            n_failed=n_stage_failed,
            wall_ms=stage_ms,
        )

    # ── Assemble + persist ChapterDraft ────────────────────────────────
    # Preserve outline order so downstream consumers can iterate sections
    # in reading order (sawc returns stage-grouped order; flatten back)
    section_order = [s["section_id"] for s in outline_sections]
    final_sections = [
        completed_sections[sid] for sid in section_order
        if sid in completed_sections
    ]

    coverage = compute_sawc_stats(
        sections=final_sections,
        n_stages=n_stages,
        n_total_drafts_fired=n_total_drafts_fired,
        n_critic_picks=n_critic_picks,
        n_picker_fallbacks=n_picker_fallbacks,
    )

    chapter_draft = ChapterDraft(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        sections=final_sections,
        memory_final=memory_ledger,
        challenges=challenges,
        flashcards=flashcards,
        coverage_stats=coverage,
    )

    payload = chapter_draft.model_dump()
    payload["outline_manifest_hash"] = outline_manifest_hash
    payload["digest_manifest_hash"]  = digest_manifest_hash
    payload["sawc_manifest_hash"]    = manifest_hash

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    elapsed = int((time.monotonic() - t0) * 1000)
    stats = {
        "n_sections":            coverage.n_sections,
        "n_completed":           coverage.n_sections_completed,
        "n_fallback":            coverage.n_sections_fallback,
        "n_stages":              coverage.n_stages,
        "n_total_drafts_fired":  coverage.n_total_drafts_fired,
        "n_critic_picks":        coverage.n_critic_picks,
        "n_picker_fallbacks":    coverage.n_picker_fallbacks,
        "n_repairs":             coverage.n_repairs,
        "total_paragraphs":      coverage.total_paragraphs,
        "total_code_refs":       coverage.total_code_refs,
        "total_citations":       coverage.total_citations,
        "avg_paragraphs_per_section": coverage.avg_paragraphs_per_section,
        "avg_chars_per_paragraph":    coverage.avg_chars_per_paragraph,
        "wall_ms":               elapsed,
        "store_path":            latest_key,
        "versioned_path":        versioned_key,
        "manifest_hash":         manifest_hash,
        "cache_hit":             False,
        "prompt_version":        SAWC_PROMPT_VERSION,
    }
    await emit_progress(
        thread_id, "sawc_write", "done",
        n_sections=stats["n_sections"],
        n_completed=stats["n_completed"],
        n_fallback=stats["n_fallback"],
        n_repairs=stats["n_repairs"],
        total_drafts_fired=stats["n_total_drafts_fired"],
        wall_ms=elapsed,
    )
    logger.info(
        f"[sawc_write] {slug}/{chapter_id}: "
        f"{stats['n_completed']}/{stats['n_sections']} sections written, "
        f"{stats['n_fallback']} fallbacks, {stats['n_repairs']} repairs, "
        f"{stats['n_total_drafts_fired']} drafts fired, "
        f"{stats['n_picker_fallbacks']} picker fallbacks, {elapsed} ms"
    )
    return {"sawc_path": latest_key, "sawc_stats": stats}


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_sawc_payload(text: str) -> dict:
    """Parse the persisted sawc blob. Returns the full payload dict;
    downstream nodes pick the fields they need (sections, memory_final,
    coverage_stats, etc.)."""
    return json.loads(text)
