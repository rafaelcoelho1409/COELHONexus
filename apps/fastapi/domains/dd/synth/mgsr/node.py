"""mgsr_replan — Memory-Guided Structure Replanner (SurveyGen-I + CoRefine).

Step 8 of the synth pipeline (per
`docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` + the mgsr_replan deep
research report). The fifth LLM-driven synth graph node, runs after
checklist_eval commits its checkpoint.

WHAT IT DOES (per chapter):

  1. Loads checklist-latest.json (from checklist_eval) +
     outline-latest.json (from outline_sdp) — both as compact JSON.
  2. FAST PATH (no LLM call): if chapter already passed checklist
     (pass_rate ≥ 0.80), construct a trivial-pass halt decision in
     <100ms and persist immediately. Most healthy chapters take
     this path.
  3. SLOW PATH: chapter failed, fire 1 batched LLM call that returns
     structured replan actions + halt decision + confidence. Pydantic-
     validate; one repair pass on cross-ref violations.
  4. Derive halt-reason via CoRefine-style cascade (confidence > 0.85,
     no_actions_needed, budget, v1_no_loop).
  5. Persists MGSRReplan to MinIO (versioned + latest pointer).
  6. Returns state patch with `mgsr_path` + `mgsr_stats`.

V1 SCOPE — no LangGraph cycle yet

  This v1 emits replan actions + halt decision but DOES NOT loop back
  to sawc_write. The cycle is deferred to v2 (StateGraph cycle +
  iteration counter in SynthState + budget tracking + best-seen
  rescue). Operator can manually re-run sawc on a modified outline,
  or apply the suggestions via a future apply_actions function.

  The fast path (trivial-pass) IS the common case — once checklist
  passes, mgsr_replan is essentially a no-op and the pipeline
  proceeds to render_audit_write.

CACHING — content-addressed:

  versioned: synth/{slug}/{chapter_id}/mgsr/{manifest_hash}.json
  latest:    synth/{slug}/{chapter_id}/mgsr-latest.json

  Manifest hash includes:
    checklist_manifest_hash
    outline_manifest_hash
    prompt_version
    schema_version

  Cache hit returns immediately + emits `done` SSE with cache_hit=true.

FAIL-SOFT BEHAVIOR:

  - LLM call fails (HTTP error): persist a fallback MGSRReplan with
    halt=true + halt_reason="confidence_high" + empty actions +
    rationale noting the LLM failure. Conservative — pipeline
    continues to render_audit_write with the current chapter as-is.
  - LLM returns malformed JSON: one repair attempt with the parse
    error as feedback. If still fails, fall back as above.
  - Cross-ref validation fails (unknown section_ids in actions):
    one repair attempt with issues as feedback. If still fails,
    persist with actions=[] + halt_reason="confidence_high" +
    issues recorded in rationale.

SSE EVENTS — real-time UI mechanism:

  start            chapter_id, chapter_title, pass_rate, chapter_passed
  trivial_pass     pass_rate (when chapter already passed → skip LLM)
  llm_request      wall_ms_so_far, n_failed_criteria
  llm_done         n_actions, halt, confidence, wall_ms, deployment,
                    repaired (bool)
  done             halt, halt_reason, n_actions, confidence, wall_ms,
                    cache_hit
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

from .constants import (
    MGSR_PROMPT_VERSION,
    MGSR_SCHEMA_VERSION,
)
from .types import (
    MGSRDecision,
    MGSRReplan,
    ReplanAction,
    _LLMReplanPayload,
)
from .service import (
    build_repair_prompt,
    build_replan_prompt,
    build_trivial_pass_decision,
    derive_halt_reason,
    is_trivial_pass,
    validate_actions_against_outline,
)
from ..observability.spans import traced
from ..progress import emit_progress
from ..state import SynthState


logger = logging.getLogger(__name__)


# =============================================================================
# Tunables
# =============================================================================
_TEMPERATURE_REPLAN   = 0.2     # mostly deterministic structural decisions
_TEMPERATURE_REPAIR   = 0.0
_MAX_TOKENS_REPLAN    = 4000
_MAX_TOKENS_REPAIR    = 4000
_MAX_REPAIR_ATTEMPTS  = 1

_BLOB_PREFIX = "synth"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# =============================================================================
# Blob keys
# =============================================================================
def _versioned_blob_key(slug: str, chapter_id: str, manifest_hash: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/mgsr/{manifest_hash}.json"


def _latest_blob_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/mgsr-latest.json"


def _checklist_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/checklist-latest.json"


def _outline_latest_key(slug: str, chapter_id: str) -> str:
    return f"{_BLOB_PREFIX}/{slug}/{chapter_id}/outline-latest.json"


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
    for err in errs[:6]:
        loc = ".".join(str(x) for x in err.get("loc", []))
        msg = err.get("msg", "")
        lines.append(f"{loc}: {msg}")
    suffix = f" (+{len(errs) - 6} more)" if len(errs) > 6 else ""
    return "; ".join(lines) + suffix


def _try_parse_payload(
    raw: dict,
) -> tuple[Optional[_LLMReplanPayload], Optional[str]]:
    try:
        return _LLMReplanPayload.model_validate(raw), None
    except ValidationError as e:
        return None, _shorten_pydantic_error(e)
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:200]}"


# =============================================================================
# Fallback decision when LLM is unavailable
# =============================================================================
def _fallback_decision(reason: str) -> MGSRDecision:
    """Conservative fallback when the LLM call fails irrecoverably.
    Emit no actions + halt the pipeline. Pipeline continues to
    render_audit_write with the current chapter as-is."""
    return MGSRDecision(
        halt=True,
        halt_reason="confidence_high",  # conservative
        confidence=0.5,
        actions=[],
        rationale_overall=(
            f"LLM-based replan unavailable: {reason}. Halting "
            f"conservatively to avoid blocking the pipeline; chapter "
            f"will be rendered as-is by render_audit_write. Operator "
            f"should review checklist_eval feedback manually."
        ),
    )


# =============================================================================
# LLM pipeline
# =============================================================================
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
) -> tuple[Optional[_LLMReplanPayload], Optional[str], bool, int]:
    """Fire the replan LLM call → parse → Pydantic → cross-ref →
    repair-if-needed.

    Returns (payload, deployment, was_repaired, wall_ms). On hard
    failure, payload is None and the caller uses _fallback_decision.
    """
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
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=_MAX_TOKENS_REPLAN,
            temperature=_TEMPERATURE_REPLAN,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[mgsr_replan] LLM call failed: {type(e).__name__}: {e}"
        )
        return None, None, False, wall_ms

    parsed = _parse_json_response(response)
    payload: Optional[_LLMReplanPayload] = None
    err: Optional[str] = None
    repaired = False

    if parsed is not None:
        payload, err = _try_parse_payload(parsed)

    # First repair: if parse OR Pydantic failed
    if payload is None and _MAX_REPAIR_ATTEMPTS > 0:
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
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp is not None:
                payload, err = _try_parse_payload(rp)
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
    if issues and _MAX_REPAIR_ATTEMPTS > 0:
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
                max_tokens=_MAX_TOKENS_REPAIR,
                temperature=_TEMPERATURE_REPAIR,
            )
            deployment = (rm or {}).get("deployment") or deployment
            rp = _parse_json_response(rr)
            if rp is not None:
                new_payload, new_err = _try_parse_payload(rp)
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
        # Filter out actions referencing unknown ids
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
        payload = _LLMReplanPayload(
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


# =============================================================================
# Manifest hash
# =============================================================================
def _compute_manifest_hash(
    *,
    checklist_manifest_hash: str,
    outline_manifest_hash: str,
) -> str:
    payload = (
        f"checklist={checklist_manifest_hash}|"
        f"outline={outline_manifest_hash}|"
        f"prompt={MGSR_PROMPT_VERSION}|"
        f"schema={MGSR_SCHEMA_VERSION}"
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


# =============================================================================
# The node
# =============================================================================
@traced("mgsr_replan")
async def mgsr_replan(state: SynthState) -> dict:
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
    checklist_key = _checklist_latest_key(slug, chapter_id)
    outline_key = _outline_latest_key(slug, chapter_id)

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
    manifest_hash = _compute_manifest_hash(
        checklist_manifest_hash=checklist_manifest_hash,
        outline_manifest_hash=outline_manifest_hash,
    )
    versioned_key = _versioned_blob_key(slug, chapter_id, manifest_hash)
    latest_key    = _latest_blob_key(slug, chapter_id)

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
        decision = _fallback_decision(
            f"LLM replan failed after {_MAX_REPAIR_ATTEMPTS} repair "
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


# =============================================================================
# Convenience loader for downstream nodes
# =============================================================================
def load_mgsr_payload(text: str) -> dict:
    """Parse the persisted mgsr blob. render_audit_write checks
    `decision.halt` to know whether to render the current chapter as
    final (halt=true) or loop back (halt=false; v2 only)."""
    return json.loads(text)
