"""mgsr — service functions (prompts, validators, halt logic, orchestrator)."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from domains.llm.rotator.chain import chat_judge_bandit_async

from ....ingestion.storage import get_storage
from ...runtime.progress import emit_progress
from ...state import SynthState

from .domain import (
    compute_manifest_hash,
    fallback_decision,
    parse_json_response,
    try_parse_payload,
)
from .keys import (
    checklist_latest_key,
    latest_blob_key,
    outline_latest_key,
    versioned_blob_key,
)
from .params import (
    CONFIDENCE_HIGH_THRESHOLD,
    MAX_ACTIONS_PER_REPLAN,
    MAX_REPAIR_ATTEMPTS,
    MAX_TOKENS_REPAIR,
    MAX_TOKENS_REPLAN,
    TEMPERATURE_REPAIR,
    TEMPERATURE_REPLAN,
)
from .schemas import (
    HaltReason,
    LLMReplanPayload,
    MGSRDecision,
    MGSRReplan,
    ReplanAction,
)
from .versions import MGSR_PROMPT_VERSION


logger = logging.getLogger(__name__)


def is_trivial_pass(checklist: dict) -> bool:
    """Fast-path predicate: pass_rate ≥ 0.80 → skip LLM replan entirely."""
    if not checklist:
        return False
    if not bool(checklist.get("chapter_passed", False)):
        return False
    pass_rate = float(checklist.get("pass_rate", 0.0))
    return pass_rate >= 0.80


def build_trivial_pass_decision(
    pass_rate: float,
) -> MGSRDecision:
    """Construct the halt decision for the trivial-pass case."""
    return MGSRDecision(
        halt=True,
        halt_reason="chapter_passed",
        confidence=1.0,
        actions=[],
        rationale_overall=(
            f"Chapter passed checklist evaluator with pass_rate "
            f"{pass_rate:.2%} ≥ 0.80 threshold. No structural replan "
            f"needed; remaining failed criteria (if any) are "
            f"acceptable below the chapter-pass threshold."
        ),
    )


def derive_halt_reason(
    payload: LLMReplanPayload,
    *,
    iteration: int = 0,
    budget: int = 5,
) -> tuple[bool, HaltReason]:
    """Combine LLM halt flag with confidence/budget rules. Cascade: budget → confidence_high → no_actions_needed → v1_no_loop."""
    if iteration >= budget:
        return True, "budget_exhausted"
    if payload.confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return True, "confidence_high"
    if payload.halt and not payload.actions:
        return True, "no_actions_needed"
    if payload.halt:
        # LLM said halt with actions emitted (suggestions for v2/manual review)
        return True, "confidence_high"
    # LLM wants to continue, but v1 doesn't loop yet
    return True, "v1_no_loop"


def validate_actions_against_outline(
    actions: list[ReplanAction],
    *,
    valid_section_ids: set[str],
) -> list[str]:
    """Return issues list: validates action targets + insert positions + new_prerequisites against valid_section_ids (checks sequential consistency)."""
    issues: list[str] = []
    available = set(valid_section_ids)

    for i, a in enumerate(actions):
        bad_targets = [t for t in a.targets if t not in available]
        if bad_targets:
            issues.append(
                f"action[{i}] ({a.action}): targets {bad_targets} "
                f"are not in the current outline OR were deleted by "
                f"an earlier action in this list."
            )
        if a.insert_after and a.insert_after not in available:
            issues.append(
                f"action[{i}] ({a.action}): insert_after "
                f"{a.insert_after!r} doesn't exist in the outline."
            )
        if a.insert_before and a.insert_before not in available:
            issues.append(
                f"action[{i}] ({a.action}): insert_before "
                f"{a.insert_before!r} doesn't exist in the outline."
            )
        if a.new_prerequisites:
            bad_prereqs = [
                p for p in a.new_prerequisites if p not in available
            ]
            if bad_prereqs:
                issues.append(
                    f"action[{i}] ({a.action}): new_prerequisites "
                    f"{bad_prereqs} don't exist in the outline."
                )

        # Simulate action's effect on `available` for next iteration
        if a.action == "delete":
            available -= set(a.targets)
        elif a.action == "merge":
            # Targets after the first are removed (merged INTO the first)
            if len(a.targets) >= 2:
                available -= set(a.targets[1:])
        # (add's new section_id is auto-assigned during apply, not pre-validated)

    return issues


# Prompt templates
def _format_outline_compact(outline_sections: list[dict]) -> str:
    """Compact outline view for the replan prompt."""
    lines: list[str] = []
    for s in outline_sections:
        sid = s.get("section_id", "?")
        heading = s.get("heading", "?")
        desc = s.get("description", "?")
        prereqs = s.get("prerequisites") or []
        prereq_str = f" (prereqs: {', '.join(prereqs)})" if prereqs else ""
        lines.append(f"  [{sid}] {heading}{prereq_str}\n      {desc}")
    return "\n".join(lines)


def _format_failed_feedback(failed_feedback: list[str]) -> str:
    """Compact failed-criteria block for the replan prompt."""
    if not failed_feedback:
        return "  (no failed criteria — chapter passed; halt expected)"
    return "\n".join(f"  - {x}" for x in failed_feedback)


def build_replan_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    pass_rate: float,
    chapter_passed: bool,
    failed_feedback: list[str],
    outline_sections: list[dict],
) -> str:
    """Build the LLM replan prompt. Used only when chapter did NOT
    trivially pass; the trivial-pass case skips this entirely."""
    outline_block = _format_outline_compact(outline_sections)
    feedback_block = _format_failed_feedback(failed_feedback)
    return (
        f"You are the Memory-Guided Structure Replanner — step 8 of "
        f"the Docs Distiller synth pipeline. The chapter just failed "
        f"checklist_eval. Your job: emit STRUCTURED ACTIONS on the "
        f"outline DAG to fix the failures, OR halt if the chapter is "
        f"good enough as-is.\n\n"

        f"Action vocabulary (verbatim from SurveyGen-I §3.2.3 "
        f"arXiv 2508.14317): merge, delete, rename, reorder, add.\n\n"

        f"FRAMEWORK: {framework}\n"
        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"PASS RATE: {pass_rate:.2%} (threshold 0.80, "
        f"chapter_passed={chapter_passed})\n\n"

        f"== FAILED CRITERIA + FEEDBACK ==\n"
        f"{feedback_block}\n\n"

        f"== CURRENT OUTLINE ==\n"
        f"{outline_block}\n\n"

        f"== ACTION VOCABULARY ==\n"
        f"- merge(targets=[s_a, s_b, ...], new_heading, new_description)\n"
        f"  Combines ≥2 sections into one. First target keeps its id; "
        f"others are removed. Downstream sections' prerequisites that "
        f"pointed to removed sections are auto-rewired to the kept id.\n\n"
        f"- delete(targets=[s_x])\n"
        f"  Removes one or more sections. Other sections' prerequisites "
        f"referencing them are auto-stripped.\n\n"
        f"- rename(targets=[s_x], new_heading?, new_description?)\n"
        f"  Just changes heading and/or description. At least one of "
        f"new_heading / new_description required.\n\n"
        f"- reorder(targets=[s_x], insert_after=s_y OR insert_before=s_y)\n"
        f"  Moves a section in reading order. Specify exactly ONE of "
        f"insert_after / insert_before.\n\n"
        f"- add(targets=[], insert_after=s_y OR insert_before=s_y, "
        f"new_heading, new_description, new_prerequisites?)\n"
        f"  Creates a new section. `targets` MUST be empty (the new id "
        f"is auto-assigned). Specify exactly ONE of insert_after / "
        f"insert_before. Use this for bridging sections that address "
        f"coherence-flow failures.\n\n"

        f"== OUTPUT — strict JSON ==\n"
        f"{{\n"
        f'  "actions": [\n'
        f'    {{\n'
        f'      "action":           "merge" | "delete" | "rename" | "reorder" | "add",\n'
        f'      "targets":          ["s_id", ...],\n'
        f'      "rationale":        "20-400 chars — which criterion this addresses",\n'
        f'      "new_heading":      "..." (when applicable, 2-8 words),\n'
        f'      "new_description":  "..." (when applicable, 20-400 chars),\n'
        f'      "new_prerequisites": ["s_id", ...] (optional for add),\n'
        f'      "insert_after":     "s_id" (when applicable),\n'
        f'      "insert_before":    "s_id" (when applicable)\n'
        f'    }},\n'
        f'    ... 0-{MAX_ACTIONS_PER_REPLAN} actions ...\n'
        f'  ],\n'
        f'  "halt":              true | false,\n'
        f'  "confidence":        0.0-1.0,\n'
        f'  "rationale_overall": "50-800 chars — strategy summary"\n'
        f"}}\n\n"

        f"== HARD RULES ==\n"
        f"1. Every action.targets[*] (and insert_after / insert_before) "
        f"MUST be an existing section_id from the outline above. "
        f"Inventing section_ids is a hard violation.\n"
        f"2. Actions are applied IN ORDER. If action[0] deletes s5, "
        f"action[1] can't reference s5.\n"
        f"3. `add` actions have EMPTY targets — the new section gets an "
        f"auto-assigned id when applied.\n"
        f"4. Surgical > broad. Emit only the MINIMUM action set that "
        f"addresses the failed criteria. 1-3 actions is typical; >5 "
        f"actions is suspicious unless the chapter is genuinely "
        f"broken.\n"
        f"5. Set `halt=true` AND emit zero actions when the chapter is "
        f"structurally sound and the failed criteria are aesthetic "
        f"preferences vs structural problems (e.g., the chapter is a "
        f"reference catalog and `chapter_reads_coherently` was a "
        f"misapplied narrative-style criterion).\n"
        f"6. `confidence` is your honest estimate that NO FURTHER "
        f"actions beyond your list would help. > 0.85 = strong halt "
        f"signal; mgsr_replan's CoRefine-style logic halts the loop "
        f"regardless of your halt flag above that threshold.\n"
        f"7. Each action's `rationale` should NAME the failed criterion "
        f"it targets (e.g., 'addresses chapter_reads_coherently failure').\n\n"

        f"Respond ONLY with valid JSON matching the schema above. NO "
        f"prose commentary, NO markdown wrapping, NO explanation."
    )


def build_repair_prompt(
    *,
    framework: str,
    chapter_id: str,
    chapter_title: str,
    pass_rate: float,
    chapter_passed: bool,
    failed_feedback: list[str],
    outline_sections: list[dict],
    current_json: str,
    issues: list[str],
) -> str:
    """Repair prompt — given an LLM replan output that failed validation,
    ask for a fixed version with the same schema."""
    outline_block = _format_outline_compact(outline_sections)
    feedback_block = _format_failed_feedback(failed_feedback)
    issues_block = "\n".join(f"- {x}" for x in issues)
    return (
        f"Fix structural issues in this replan output. Keep the same JSON "
        f"schema. Preserve good actions; only change what's needed to "
        f"clear the issues below.\n\n"

        f"CHAPTER: {chapter_id} — {chapter_title}\n"
        f"FRAMEWORK: {framework}\n"
        f"PASS RATE: {pass_rate:.2%} (chapter_passed={chapter_passed})\n\n"

        f"FAILED CRITERIA:\n{feedback_block}\n\n"
        f"CURRENT OUTLINE (use ONLY these section_ids):\n"
        f"{outline_block}\n\n"

        f"CURRENT REPLAN:\n{current_json}\n\n"

        f"ISSUES TO FIX:\n{issues_block}\n\n"

        f"Respond ONLY with valid JSON matching the original schema. "
        f"NO commentary, NO markdown wrapping."
    )


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
    try:
        response, meta = await chat_judge_bandit_async(
            prompt,
            max_tokens=MAX_TOKENS_REPLAN,
            temperature=TEMPERATURE_REPLAN,
        )
        deployment = (meta or {}).get("deployment")
    except Exception as e:
        wall_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            f"[mgsr_replan] LLM call failed: {type(e).__name__}: {e}"
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
