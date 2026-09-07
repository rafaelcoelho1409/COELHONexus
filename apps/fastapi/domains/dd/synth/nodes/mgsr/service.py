"""mgsr — service functions (prompts, validators, halt logic, orchestrator)."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState

from .domain import (
    build_trivial_pass_decision,
    compute_manifest_hash,
    derive_halt_reason,
    fallback_decision,
    is_trivial_pass,
    parse_json_response,
    try_parse_payload,
    validate_actions_against_outline,
)
from .keys import (
    checklist_latest_key,
    latest_blob_key,
    outline_latest_key,
    versioned_blob_key,
)
from .params import (
    MAX_REPAIR_ATTEMPTS,
    MAX_TOKENS_REPAIR,
    MAX_TOKENS_REPLAN,
    TEMPERATURE_REPAIR,
    TEMPERATURE_REPLAN,
    TIMEOUT_S_REPAIR,
    TIMEOUT_S_REPLAN,
)
from .prompts import build_repair_prompt, build_replan_prompt
from .schemas import (
    LLMReplanPayload,
    MGSRDecision,
    MGSRReplan,
    ReplanAction,
)
from .versions import MGSR_PROMPT_VERSION


logger = logging.getLogger(__name__)

# Replan-call attempts before falling back to fallback_decision(). Same
# retry idiom as outline_sdp/digest_construct/sawc_write/checklist_eval.
_MAX_CALL_ATTEMPTS = 2


async def _run_llm_replan(
    *,
    thread_id: str,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    pass_rate: float,
    chapter_passed: bool,
    failed_feedback: list[str],
    outline_sections: list[dict],
    valid_section_ids: set[str],
) -> tuple[Optional[LLMReplanPayload], Optional[str], bool, int]:
    """Fire replan LLM call → parse → Pydantic → cross-ref → repair if needed. Returns (payload, deployment, was_repaired, wall_ms); None payload → caller uses fallback_decision."""
    t0 = time.monotonic()
    prompt = build_replan_prompt(
        framework=framework,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        pass_rate=pass_rate,
        chapter_passed=chapter_passed,
        failed_feedback=failed_feedback,
        outline_sections=outline_sections,
    )

    deployment: Optional[str] = None
    last_error: Optional[Exception] = None
    for call_attempt in range(_MAX_CALL_ATTEMPTS):
        try:
            response, meta = await chat_judge_bandit_async(
                prompt,
                max_tokens=MAX_TOKENS_REPLAN,
                temperature=TEMPERATURE_REPLAN,
                timeout_s=TIMEOUT_S_REPLAN,
            )
            deployment = (meta or {}).get("deployment")
            last_error = None
            break
        except Exception as e:
            last_error = e
            if call_attempt < _MAX_CALL_ATTEMPTS - 1:
                # This node fires once per chapter per CoRefine iteration
                # (not per-source/per-section like its siblings), so a
                # cheap retry costs little — same idiom as
                # outline_sdp/digest_construct/sawc_write/checklist_eval.
                # The prompt here is small/bounded (compact outline
                # metadata only, no vault bodies), so this only needs a
                # plain backoff, not a context-overflow-aware rebuild.
                await asyncio.sleep(1.0 + random.random())
    if last_error is not None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[mgsr_replan] LLM call failed after {_MAX_CALL_ATTEMPTS} "
            f"attempt(s): {type(last_error).__name__}: {last_error}"
        )
        return None, None, False, wall_ms

    parsed = parse_json_response(response)
    payload: Optional[LLMReplanPayload] = None
    err: Optional[str] = None
    repaired = False

    if parsed is not None:
        payload, err = try_parse_payload(parsed)

    # First repair: if parse OR Pydantic failed
    if payload is None and MAX_REPAIR_ATTEMPTS > 0:
        repair_issues = [
            err if err else "previous response was not parseable JSON"
        ]
        current_json = json.dumps(parsed or {"_raw": (response or "")[:400]})
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            pass_rate=pass_rate,
            chapter_passed=chapter_passed,
            failed_feedback=failed_feedback,
            outline_sections=outline_sections,
            current_json=current_json,
            issues=repair_issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=MAX_TOKENS_REPAIR,
                temperature=TEMPERATURE_REPAIR,
                timeout_s=TIMEOUT_S_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = parse_json_response(rr)
            if rp is not None:
                payload, err = try_parse_payload(rp)
                if payload is not None:
                    repaired = True
        except Exception as e:
            logger.warning(
                f"[mgsr_replan] repair (parse/pydantic) failed: "
                f"{type(e).__name__}: {e}"
            )

    if payload is None:
        wall_ms = int((time.monotonic() - t0) * 1000)
        return None, deployment, False, wall_ms

    # Second-stage validation: cross-ref actions against outline
    issues = validate_actions_against_outline(
        payload.actions, valid_section_ids=valid_section_ids,
    )
    if issues and MAX_REPAIR_ATTEMPTS > 0:
        repair_prompt = build_repair_prompt(
            framework=framework,
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            pass_rate=pass_rate,
            chapter_passed=chapter_passed,
            failed_feedback=failed_feedback,
            outline_sections=outline_sections,
            current_json=json.dumps(payload.model_dump()),
            issues=issues,
        )
        try:
            rr, rm = await chat_judge_bandit_async(
                repair_prompt,
                max_tokens=MAX_TOKENS_REPAIR,
                temperature=TEMPERATURE_REPAIR,
                timeout_s=TIMEOUT_S_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = parse_json_response(rr)
            if rp is not None:
                new_payload, new_err = try_parse_payload(rp)
                if new_payload is not None:
                    new_issues = validate_actions_against_outline(
                        new_payload.actions,
                        valid_section_ids=valid_section_ids,
                    )
                    # Accept only if strict improvement
                    if len(new_issues) < len(issues):
                        payload = new_payload
                        repaired = True
                        issues = new_issues
        except Exception as e:
            logger.warning(
                f"[mgsr_replan] repair (cross-ref) failed: "
                f"{type(e).__name__}: {e}"
            )

    # If issues STILL remain after repair, drop the offending actions
    # rather than ship invalid actions. Surface in rationale.
    if issues:
        kept_actions: list[ReplanAction] = []
        available = set(valid_section_ids)
        for a in payload.actions:
            ok = True
            for t in a.targets:
                if t not in available:
                    ok = False
                    break
            if a.insert_after and a.insert_after not in available:
                ok = False
            if a.insert_before and a.insert_before not in available:
                ok = False
            if ok:
                kept_actions.append(a)
                if a.action == "delete":
                    available -= set(a.targets)
                elif a.action == "merge" and len(a.targets) >= 2:
                    available -= set(a.targets[1:])
        # Reconstruct payload with filtered actions + appended rationale
        dropped = len(payload.actions) - len(kept_actions)
        if dropped:
            logger.info(
                f"[mgsr_replan] dropped {dropped} action(s) with "
                f"unresolved cross-ref issues: {issues[:2]}"
            )
        payload = LLMReplanPayload(
            actions=kept_actions,
            halt=payload.halt,
            confidence=payload.confidence,
            rationale_overall=(
                payload.rationale_overall
                + f" [Note: {dropped} action(s) auto-dropped by mgsr_replan "
                  f"for unresolved cross-ref issues.]"
            )[:800],
        )

    wall_ms = int((time.monotonic() - t0) * 1000)
    return payload, deployment, repaired, wall_ms


async def mgsr_replan_run(state: SynthState) -> dict:
    """Run the Memory-Guided Structure Replanner for one chapter."""
    slug = state.get("framework_slug")
    chapter_id = state.get("chapter_id")
    thread_id = state.get("thread_id") or ""

    if not slug or not chapter_id:
        return {
            "mgsr_path":  "",
            "mgsr_stats": {
                "skipped": "no_slug_or_chapter_id", "wall_ms": 0,
            },
            "status": "failed",
            "error":  "framework_slug or chapter_id missing from SynthState",
        }

    t0 = time.monotonic()
    minio = get_storage()

    # ── Load checklist + outline ───────────────────────────────────────
    checklist_key = checklist_latest_key(slug, chapter_id)
    outline_key = outline_latest_key(slug, chapter_id)

    if not await minio.exists(checklist_key):
        return {
            "mgsr_path":  "",
            "mgsr_stats": {
                "skipped":       "checklist_not_found",
                "checklist_key": checklist_key,
                "wall_ms":       int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"checklist {checklist_key!r} not in MinIO — run "
                      f"checklist_eval first",
        }
    if not await minio.exists(outline_key):
        return {
            "mgsr_path":  "",
            "mgsr_stats": {
                "skipped":     "outline_not_found",
                "outline_key": outline_key,
                "wall_ms":     int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"outline {outline_key!r} not in MinIO — run "
                      f"outline_sdp first",
        }

    try:
        checklist_text = await minio.read_text(checklist_key)
        checklist = json.loads(checklist_text)
        outline_text = await minio.read_text(outline_key)
        outline_payload = json.loads(outline_text)
    except Exception as e:
        return {
            "mgsr_path":  "",
            "mgsr_stats": {
                "skipped": "inputs_unreadable",
                "wall_ms": int((time.monotonic() - t0) * 1000),
            },
            "status": "failed",
            "error":  f"checklist/outline unreadable: {type(e).__name__}: {e}",
        }

    outline_data = outline_payload.get("outline") or {}
    outline_sections = outline_data.get("sections") or []
    valid_section_ids = {
        s.get("section_id", "") for s in outline_sections
        if s.get("section_id")
    }
    chapter_title = (
        checklist.get("chapter_title")
        or outline_payload.get("chapter_title")
        or chapter_id
    )
    pass_rate = float(checklist.get("pass_rate", 0.0))
    chapter_passed = bool(checklist.get("chapter_passed", False))
    failed_feedback = list(checklist.get("failed_feedback") or [])
    n_failed = len(failed_feedback)
    failed_names = [
        c.get("name", "?")
        for c in (checklist.get("criteria") or [])
        if not c.get("passed", False)
    ]
    checklist_manifest_hash = (
        checklist.get("checklist_manifest_hash") or ""
    )
    outline_manifest_hash = outline_payload.get("manifest_hash") or ""

    await emit_progress(
        thread_id, "mgsr_replan", "start",
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        pass_rate=pass_rate,
        chapter_passed=chapter_passed,
        n_failed_criteria=n_failed,
    )

    # ── Cache fast-path ────────────────────────────────────────────────
    manifest_hash = compute_manifest_hash(
        checklist_manifest_hash=checklist_manifest_hash,
        outline_manifest_hash=outline_manifest_hash,
    )
    versioned_key = versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = latest_blob_key(slug, chapter_id)

    if await minio.exists(versioned_key) and await minio.exists(latest_key):
        try:
            cached_text = await minio.read_text(versioned_key)
            cached = json.loads(cached_text)
            decision = (cached or {}).get("decision") or {}
            elapsed = int((time.monotonic() - t0) * 1000)
            stats = {
                "halt":              decision.get("halt", True),
                "halt_reason":       decision.get("halt_reason", "?"),
                "confidence":        decision.get("confidence", 0.0),
                "n_actions":         len(decision.get("actions") or []),
                "chapter_passed":    cached.get("checklist_chapter_passed", False),
                "pass_rate":         cached.get("checklist_pass_rate", 0.0),
                "wall_ms":           elapsed,
                "store_path":        latest_key,
                "versioned_path":    versioned_key,
                "manifest_hash":     manifest_hash,
                "cache_hit":         True,
                "prompt_version":    cached.get("prompt_version"),
            }
            await emit_progress(
                thread_id, "mgsr_replan", "done",
                halt=stats["halt"],
                halt_reason=stats["halt_reason"],
                n_actions=stats["n_actions"],
                confidence=stats["confidence"],
                wall_ms=elapsed, cache_hit=True,
            )
            logger.info(
                f"[mgsr_replan] {slug}/{chapter_id}: CACHE HIT — "
                f"halt={stats['halt']} reason={stats['halt_reason']!r} "
                f"actions={stats['n_actions']} conf={stats['confidence']:.2f} "
                f"{elapsed} ms"
            )
            return {"mgsr_path": latest_key, "mgsr_stats": stats}
        except Exception as e:
            logger.warning(
                f"[mgsr_replan] {slug}/{chapter_id}: cached blob "
                f"{versioned_key!r} unreadable ({type(e).__name__}: {e}); "
                f"recomputing"
            )

    # ── Fast path: chapter already passed checklist (no LLM call) ──────
    if is_trivial_pass(checklist):
        decision = build_trivial_pass_decision(pass_rate)
        await emit_progress(
            thread_id, "mgsr_replan", "trivial_pass",
            pass_rate=pass_rate,
        )
        replan = MGSRReplan(
            chapter_id=chapter_id,
            chapter_title=chapter_title,
            framework_slug=slug,
            iteration=0,
            decision=decision,
            deployment=None,
            wall_ms=int((time.monotonic() - t0) * 1000),
            checklist_pass_rate=pass_rate,
            checklist_chapter_passed=chapter_passed,
            n_failed_criteria=n_failed,
            failed_criteria_names=failed_names,
        )
        payload = replan.model_dump()
        payload["checklist_manifest_hash"] = checklist_manifest_hash
        payload["outline_manifest_hash"]   = outline_manifest_hash
        payload["mgsr_manifest_hash"]      = manifest_hash

        blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
        await minio.write(
            versioned_key, blob_bytes, content_type="application/json",
        )
        await minio.write(
            latest_key, blob_bytes, content_type="application/json",
        )

        elapsed = int((time.monotonic() - t0) * 1000)
        stats = {
            "halt":           True,
            "halt_reason":    "chapter_passed",
            "confidence":     1.0,
            "n_actions":      0,
            "chapter_passed": chapter_passed,
            "pass_rate":      pass_rate,
            "n_failed_criteria": n_failed,
            "wall_ms":        elapsed,
            "store_path":     latest_key,
            "versioned_path": versioned_key,
            "manifest_hash":  manifest_hash,
            "cache_hit":      False,
            "prompt_version": MGSR_PROMPT_VERSION,
            "trivial_pass":   True,
        }
        await emit_progress(
            thread_id, "mgsr_replan", "done",
            halt=True,
            halt_reason="chapter_passed",
            n_actions=0,
            confidence=1.0,
            wall_ms=elapsed,
        )
        logger.info(
            f"[mgsr_replan] {slug}/{chapter_id}: TRIVIAL PASS "
            f"(pass_rate={pass_rate:.2%} ≥ 0.80), no LLM call, "
            f"{elapsed} ms"
        )
        return {"mgsr_path": latest_key, "mgsr_stats": stats}

    # ── Slow path: chapter failed checklist; fire LLM replan ───────────
    await emit_progress(
        thread_id, "mgsr_replan", "llm_request",
        wall_ms_so_far=int((time.monotonic() - t0) * 1000),
        n_failed_criteria=n_failed,
    )

    llm_payload, deployment, repaired, llm_wall_ms = await _run_llm_replan(
        thread_id=thread_id,
        framework=slug,
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        pass_rate=pass_rate,
        chapter_passed=chapter_passed,
        failed_feedback=failed_feedback,
        outline_sections=outline_sections,
        valid_section_ids=valid_section_ids,
    )

    if llm_payload is None:
        # Hard failure — emit fallback decision
        decision = fallback_decision(
            f"LLM replan failed after {MAX_REPAIR_ATTEMPTS} repair "
            f"attempt(s)"
        )
        await emit_progress(
            thread_id, "mgsr_replan", "llm_done",
            n_actions=0,
            halt=True,
            confidence=decision.confidence,
            wall_ms=llm_wall_ms,
            deployment=deployment,
            repaired=False,
            error="llm_unavailable",
        )
    else:
        halt, halt_reason = derive_halt_reason(llm_payload, iteration=0)
        decision = MGSRDecision(
            halt=halt,
            halt_reason=halt_reason,
            confidence=llm_payload.confidence,
            actions=llm_payload.actions,
            rationale_overall=llm_payload.rationale_overall,
        )
        await emit_progress(
            thread_id, "mgsr_replan", "llm_done",
            n_actions=len(llm_payload.actions),
            halt=halt,
            halt_reason=halt_reason,
            confidence=llm_payload.confidence,
            wall_ms=llm_wall_ms,
            deployment=deployment,
            repaired=repaired,
        )

    # ── Persist ────────────────────────────────────────────────────────
    elapsed = int((time.monotonic() - t0) * 1000)
    replan = MGSRReplan(
        chapter_id=chapter_id,
        chapter_title=chapter_title,
        framework_slug=slug,
        iteration=0,
        decision=decision,
        deployment=deployment,
        wall_ms=elapsed,
        checklist_pass_rate=pass_rate,
        checklist_chapter_passed=chapter_passed,
        n_failed_criteria=n_failed,
        failed_criteria_names=failed_names,
    )
    payload = replan.model_dump()
    payload["checklist_manifest_hash"] = checklist_manifest_hash
    payload["outline_manifest_hash"]   = outline_manifest_hash
    payload["mgsr_manifest_hash"]      = manifest_hash

    blob_bytes = json.dumps(payload, indent=2, ensure_ascii=False)
    await minio.write(
        versioned_key, blob_bytes, content_type="application/json",
    )
    await minio.write(
        latest_key, blob_bytes, content_type="application/json",
    )

    stats = {
        "halt":            decision.halt,
        "halt_reason":     decision.halt_reason,
        "confidence":      decision.confidence,
        "n_actions":       len(decision.actions),
        "chapter_passed":  chapter_passed,
        "pass_rate":       pass_rate,
        "n_failed_criteria": n_failed,
        "wall_ms":         elapsed,
        "store_path":      latest_key,
        "versioned_path":  versioned_key,
        "manifest_hash":   manifest_hash,
        "cache_hit":       False,
        "prompt_version":  MGSR_PROMPT_VERSION,
        "deployment":      deployment,
        "repaired":        repaired,
        "trivial_pass":    False,
    }
    await emit_progress(
        thread_id, "mgsr_replan", "done",
        halt=decision.halt,
        halt_reason=decision.halt_reason,
        n_actions=len(decision.actions),
        confidence=decision.confidence,
        wall_ms=elapsed,
    )
    logger.info(
        f"[mgsr_replan] {slug}/{chapter_id}: "
        f"halt={decision.halt} reason={decision.halt_reason!r} "
        f"actions={len(decision.actions)} conf={decision.confidence:.2f} "
        f"(pass_rate={pass_rate:.2%}, {n_failed} failed criteria); "
        f"{elapsed} ms"
    )
    return {"mgsr_path": latest_key, "mgsr_stats": stats}
