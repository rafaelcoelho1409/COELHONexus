"""mgsr — service functions (prompts, validators, halt logic)."""
from __future__ import annotations

from .constants import (
    _CONFIDENCE_HIGH_THRESHOLD,
    _MAX_ACTIONS_PER_REPLAN,
)
from .types import (
    MGSRDecision,
    ReplanAction,
    _LLMReplanPayload,
    HaltReason,
)


# =============================================================================
# Trivial-pass fast path
# =============================================================================
def is_trivial_pass(checklist: dict) -> bool:
    """Fast-path predicate: chapter already passed the checklist, no
    replan needed. Avoids the LLM call entirely.

    Per the SOTA doc threshold: pass_rate ≥ 0.80 is shippable.
    """
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


# =============================================================================
# Halt-reason derivation (when LLM was consulted)
# =============================================================================
def derive_halt_reason(
    payload: _LLMReplanPayload,
    *,
    iteration: int = 0,
    budget: int = 5,
) -> tuple[bool, HaltReason]:
    """Combine the LLM's emitted `halt` flag with confidence-based and
    budget-based halt rules. Returns (halt, halt_reason).

    Halt cascade (in priority order):
      1. iteration ≥ budget → budget_exhausted
      2. confidence ≥ _CONFIDENCE_HIGH_THRESHOLD → confidence_high
      3. halt==true + no actions → no_actions_needed
      4. halt==true + actions emitted → confidence_high (LLM said halt)
      5. Otherwise (v1) → v1_no_loop (we emit actions but don't cycle)
    """
    if iteration >= budget:
        return True, "budget_exhausted"
    if payload.confidence >= _CONFIDENCE_HIGH_THRESHOLD:
        return True, "confidence_high"
    if payload.halt and not payload.actions:
        return True, "no_actions_needed"
    if payload.halt:
        # LLM said halt with actions emitted (suggestions for v2/manual review)
        return True, "confidence_high"
    # LLM wants to continue, but v1 doesn't loop yet
    return True, "v1_no_loop"


# =============================================================================
# Cross-reference validators (post-Pydantic, fail-soft for repair loop)
# =============================================================================
def validate_actions_against_outline(
    actions: list[ReplanAction],
    *,
    valid_section_ids: set[str],
) -> list[str]:
    """Return list of issue strings suitable for repair-prompt feedback.

    Checks:
      - every action.targets[i] is in valid_section_ids
      - every insert_after / insert_before is in valid_section_ids
      - every new_prerequisites entry is in valid_section_ids (for `add`)
      - sequential-application sanity (action[N] doesn't reference
        section_ids deleted by earlier actions)
    """
    issues: list[str] = []
    available = set(valid_section_ids)

    for i, a in enumerate(actions):
        # Check targets
        bad_targets = [t for t in a.targets if t not in available]
        if bad_targets:
            issues.append(
                f"action[{i}] ({a.action}): targets {bad_targets} "
                f"are not in the current outline OR were deleted by "
                f"an earlier action in this list."
            )
        # Check insertion anchors
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
        # Check new_prerequisites
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
        # add / rename / reorder don't remove section_ids
        # (add's new section_id is auto-assigned during apply, not pre-validated)

    return issues


# =============================================================================
# Prompt templates
# =============================================================================
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
        f'    ... 0-{_MAX_ACTIONS_PER_REPLAN} actions ...\n'
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
