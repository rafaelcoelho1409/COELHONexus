"""mgsr — LLM prompt builders (replan + repair) and the small compact
formatters they consume."""
from __future__ import annotations

from .params import MAX_ACTIONS_PER_REPLAN


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
        f"insert_before.\n\n"

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
        f"addresses the failed criteria. 1-3 actions is typical; >5 is "
        f"suspicious.\n"
        f"5. Set `halt=true` AND emit zero actions when the chapter is "
        f"structurally sound and the failed criteria are aesthetic.\n"
        f"6. `confidence` is your honest estimate that NO FURTHER actions "
        f"beyond your list would help. > 0.85 = strong halt signal.\n"
        f"7. Each action's `rationale` should NAME the failed criterion "
        f"it targets.\n\n"

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
        f"Fix structural issues in this replan output. Keep the same "
        f"JSON schema. Preserve good actions; only change what's needed "
        f"to clear the issues below.\n\n"

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
