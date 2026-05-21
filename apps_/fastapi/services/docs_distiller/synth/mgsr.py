"""mgsr_replan — Memory-Guided Structure Replanner library.

Pure module: Pydantic schemas + prompt templates + deterministic
validators + halt-decision helper. No I/O, no LLM calls — that lives
in `synth/nodes/mgsr_replan.py`.

ARCHITECTURE — three primitives layered

  1. SurveyGen-I MGSR (arXiv 2508.14317 §3.2.3) — typed action vocabulary
     {merge, delete, rename, reorder, add} on the outline DAG. Verbatim
     from the paper: "MGSR produces a set of structured revision
     actions applied to remaining outline sections."

  2. CoRefine (arXiv 2602.08948, Feb 2026) — confidence-guided halting.
     CoRefine's original controller uses LLM logprobs (which we can't
     access through the LiteLLM rotator), so v1 uses surrogate signals:
       - pass_rate ≥ 0.80 → trivial pass, halt
       - LLM-self-reported confidence > 0.85 → halt
       - empty action list AND confidence > 0.7 → halt
       - iteration ≥ budget → halt (budget tracking deferred to v2 loop)

  3. CheckEval-style structured output — JSON-schema-constrained
     replan actions with Pydantic enforcement so mgsr_replan's
     output is mechanically applicable to the outline (deterministic
     action-application function — no LLM re-parse on next iter).

WHY THIS REPLACES DEPRECATED ADJUSTMENT_PROMPT

  Deprecated ADJUSTMENT_PROMPT pattern:
    - Free-form markdown bullets: `**[dim]** Find: "span" → Apply: suggestion`
    - CRITIC-pattern span-anchored suggestions
    - Refiner LLM re-parses bullets to apply on next iter
    - Halt: OP-7 audit-regression early-stop (heuristic 1.2× ratio)

  New mgsr_replan:
    - Typed ReplanAction objects (Pydantic-validated)
    - Operate on outline DAG (structural) NOT prose spans (micro-edits)
    - Actions are MECHANICALLY applied (apply_actions function, v2)
    - Halt: CoRefine-style confidence/plateau (principled, not heuristic)

V1 SCOPE

  This v1 emits the actions + halt decision but DOES NOT loop. The
  LangGraph cycle `mgsr_replan → sawc_write` is deferred to v2 to
  avoid the StateGraph-cycle + iteration-tracking complexity. The
  operator can manually re-run sawc on the modified outline (or
  inspect the actions and decide whether to invest in v2).

  Trivial-pass case (chapter already passed checklist):
    NO LLM call, fast path returns halt(chapter_passed) in <100ms.

  Failed-checklist case:
    1 LLM call returns structured replan actions + confidence,
    persisted for v2 consumption.

INPUTS / OUTPUTS

  Input (read by the node):
    checklist-latest.json  — pass_rate, chapter_passed, failed_feedback,
                              per-criterion verdicts
    outline-latest.json    — current sections + DAG (for action targets)

  Output per chapter (persisted):
    MGSRReplan{
      decision: MGSRDecision{halt, halt_reason, confidence, actions,
                              rationale_overall},
      iteration: 0,  // v1 always 0
      checklist_pass_rate, checklist_chapter_passed,
      deployment, wall_ms, ...
    }

DOWNSTREAM CONSUMER (next node):

  - `render_audit_write` reads mgsr-latest.json. If decision.halt is
    true → renders the current chapter as final markdown. Else (v2)
    → cycle back to sawc_write with the updated outline.

TUNABLES

  _CONFIDENCE_HIGH_THRESHOLD     = 0.85  → halt
  _CONFIDENCE_PLATEAU_THRESHOLD  = 0.70  → halt only if no actions
  _MAX_ACTIONS_PER_REPLAN        = 10    (per SurveyGen-I + MAMM
                                            "surgical > broad" guidance)
  _RATIONALE_PER_ACTION_CHARS    = 20-400
  _RATIONALE_OVERALL_CHARS       = 50-800
  _HEADING_MIN/MAX_WORDS         = 2-8   (matches outline_sdp)
  _DESCRIPTION_CHARS             = 20-400 (matches outline_sdp)
"""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# =============================================================================
# Versioning + tunables
# =============================================================================
MGSR_SCHEMA_VERSION = "1.0"
MGSR_PROMPT_VERSION = "v1-2026-05-19"

_CONFIDENCE_HIGH_THRESHOLD = 0.85
_CONFIDENCE_PLATEAU_THRESHOLD = 0.70
_MAX_ACTIONS_PER_REPLAN = 10
_RATIONALE_MIN_CHARS = 20
_RATIONALE_MAX_CHARS = 400
_RATIONALE_OVERALL_MIN_CHARS = 50
_RATIONALE_OVERALL_MAX_CHARS = 800
_HEADING_MIN_WORDS = 2
_HEADING_MAX_WORDS = 8
_DESCRIPTION_MIN_CHARS = 20
_DESCRIPTION_MAX_CHARS = 400
_MIN_TARGETS = {
    "merge":   2,    # combining requires ≥2 sections
    "delete":  1,
    "rename":  1,
    "reorder": 1,
    "add":     0,    # `add` doesn't operate on existing targets
}
_MAX_TARGETS_PER_ACTION = 8

_SECTION_ID_RE = re.compile(r"^s\d{1,3}$")


ReplanActionType = Literal["merge", "delete", "rename", "reorder", "add"]
HaltReason = Literal[
    "chapter_passed",          # trivial — pass_rate ≥ 0.80, no LLM call
    "no_actions_needed",       # LLM emitted zero actions, halt=true
    "confidence_high",         # LLM confidence > _CONFIDENCE_HIGH_THRESHOLD
    "confidence_plateau",      # iteration history shows plateau (v2 only)
    "budget_exhausted",        # iteration count hit cap (v2 only)
    "v1_no_loop",              # actions emitted but v1 doesn't loop yet
]


# =============================================================================
# Pydantic — LLM output side (private)
# =============================================================================
class ReplanAction(BaseModel):
    """One structured replan action over the outline DAG.

    Action vocabulary verbatim from SurveyGen-I §3.2.3:
      - merge:   combine targets into one section (first target = new id)
      - delete:  remove target section(s); cleanup downstream prereqs
      - rename:  change heading/description of a section
      - reorder: move a section in reading order (insert_before/after)
      - add:     create a new section (insert position required)

    Action-specific required fields enforced by `_validate_action_fields`
    below. v2's apply_actions function consumes this object directly —
    no further LLM parsing needed.
    """
    action:    ReplanActionType
    targets:   list[str] = Field(
        default_factory=list,
        description=(
            "section_ids the action operates on. `add` may have empty "
            "targets; others must reference EXISTING section_ids from "
            "the current outline. Listed in the prompt's `available "
            "section_ids` block."
        ),
    )
    rationale: str = Field(
        description=(
            f"{_RATIONALE_MIN_CHARS}-{_RATIONALE_MAX_CHARS} chars. Why "
            f"THIS specific action — which failed criterion does it "
            f"address. Used by mgsr_replan's persistence (debugging "
            f"+ render_audit_write provenance)."
        ),
    )
    new_heading:        Optional[str] = Field(default=None,
        description="REQUIRED for merge/rename/add. 2-8 words, like outline_sdp's heading rules.")
    new_description:    Optional[str] = Field(default=None,
        description="REQUIRED for merge/rename/add. 20-400 chars, like outline_sdp's description rules.")
    new_prerequisites:  Optional[list[str]] = Field(default=None,
        description="OPTIONAL for add. List of section_ids the new section depends on.")
    insert_after:       Optional[str] = Field(default=None,
        description="REQUIRED for reorder/add (exactly one of insert_after/insert_before). "
                    "section_id that this section should appear AFTER in reading order.")
    insert_before:      Optional[str] = Field(default=None,
        description="REQUIRED for reorder/add (exactly one of insert_after/insert_before). "
                    "section_id that this section should appear BEFORE.")

    @field_validator("targets")
    @classmethod
    def _validate_targets_format(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _SECTION_ID_RE.match(t):
                raise ValueError(
                    f"target {t!r} must match section_id format /^s\\d+$/"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate targets in same action: {v}")
        return v

    @field_validator("rationale")
    @classmethod
    def _validate_rationale(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_RATIONALE_MIN_CHARS <= len(s) <= _RATIONALE_MAX_CHARS):
            raise ValueError(
                f"rationale must be {_RATIONALE_MIN_CHARS}-"
                f"{_RATIONALE_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("new_heading")
    @classmethod
    def _validate_heading(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        words = s.split()
        if not (_HEADING_MIN_WORDS <= len(words) <= _HEADING_MAX_WORDS):
            raise ValueError(
                f"new_heading must be {_HEADING_MIN_WORDS}-"
                f"{_HEADING_MAX_WORDS} words; got {len(words)} ({s!r})"
            )
        if s.startswith("#"):
            raise ValueError("new_heading must NOT start with '#'")
        return s

    @field_validator("new_description")
    @classmethod
    def _validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = " ".join(v.strip().split())
        if not (_DESCRIPTION_MIN_CHARS <= len(s) <= _DESCRIPTION_MAX_CHARS):
            raise ValueError(
                f"new_description must be {_DESCRIPTION_MIN_CHARS}-"
                f"{_DESCRIPTION_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("new_prerequisites")
    @classmethod
    def _validate_prereqs(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return None
        for p in v:
            if not _SECTION_ID_RE.match(p):
                raise ValueError(
                    f"new_prerequisites entry {p!r} must match section_id format"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate prerequisites: {v}")
        return v

    @model_validator(mode="after")
    def _validate_action_fields(self) -> "ReplanAction":
        """Enforce action-specific required-field combinations."""
        min_targets = _MIN_TARGETS[self.action]
        if len(self.targets) < min_targets:
            raise ValueError(
                f"action {self.action!r} requires ≥{min_targets} targets; "
                f"got {len(self.targets)}"
            )
        if len(self.targets) > _MAX_TARGETS_PER_ACTION:
            raise ValueError(
                f"action {self.action!r} has {len(self.targets)} targets; "
                f"max {_MAX_TARGETS_PER_ACTION}"
            )

        # Action-specific required fields
        if self.action == "merge":
            if not self.new_heading:
                raise ValueError("merge requires new_heading")
            if not self.new_description:
                raise ValueError("merge requires new_description")
        elif self.action == "rename":
            if not (self.new_heading or self.new_description):
                raise ValueError(
                    "rename requires new_heading OR new_description"
                )
        elif self.action == "reorder":
            if not (self.insert_after or self.insert_before):
                raise ValueError(
                    "reorder requires insert_after OR insert_before"
                )
            if self.insert_after and self.insert_before:
                raise ValueError(
                    "reorder must specify EXACTLY ONE of insert_after / insert_before"
                )
        elif self.action == "add":
            if self.targets:
                raise ValueError(
                    "add must have empty `targets` (the new section gets "
                    "an auto-assigned id during apply_actions)"
                )
            if not self.new_heading:
                raise ValueError("add requires new_heading")
            if not self.new_description:
                raise ValueError("add requires new_description")
            if not (self.insert_after or self.insert_before):
                raise ValueError(
                    "add requires insert_after OR insert_before"
                )
            if self.insert_after and self.insert_before:
                raise ValueError(
                    "add must specify EXACTLY ONE of insert_after / insert_before"
                )
        # delete has no extra required fields beyond targets ≥ 1

        return self


class _LLMReplanPayload(BaseModel):
    """What the LLM emits when it has to replan. The node wraps this
    into the final MGSRDecision after computing halt-reason."""
    actions: list[ReplanAction] = Field(
        default_factory=list,
        description=(
            f"0-{_MAX_ACTIONS_PER_REPLAN} replan actions. Surgical > "
            f"broad — emit only the minimum set that addresses the "
            f"failed criteria. Empty list with halt=true is the "
            f"correct response when the chapter is structurally fine "
            f"and the failed criteria are aesthetic preferences."
        ),
    )
    halt:              bool = Field(
        description=(
            "Whether to STOP the replan loop. true = chapter is good "
            "enough or no actions would meaningfully help. false = "
            "the emitted actions would improve quality if applied."
        ),
    )
    confidence:        float = Field(
        ge=0.0, le=1.0,
        description=(
            "Self-reported confidence (0-1) that no further actions "
            "are needed beyond the ones listed. Used by mgsr_replan's "
            "CoRefine-style halt logic (> 0.85 → halt regardless of "
            "halt flag value)."
        ),
    )
    rationale_overall: str = Field(
        description=(
            f"{_RATIONALE_OVERALL_MIN_CHARS}-{_RATIONALE_OVERALL_MAX_CHARS} "
            f"chars. 1-paragraph summary of the replan strategy. Why "
            f"these specific actions (or none), and what you expect to "
            f"improve on the next iteration if applied."
        ),
    )

    @field_validator("actions")
    @classmethod
    def _validate_action_count(
        cls, v: list[ReplanAction],
    ) -> list[ReplanAction]:
        if len(v) > _MAX_ACTIONS_PER_REPLAN:
            raise ValueError(
                f"actions count {len(v)} exceeds max "
                f"{_MAX_ACTIONS_PER_REPLAN}"
            )
        return v

    @field_validator("rationale_overall")
    @classmethod
    def _validate_overall(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (_RATIONALE_OVERALL_MIN_CHARS <= len(s) <= _RATIONALE_OVERALL_MAX_CHARS):
            raise ValueError(
                f"rationale_overall must be {_RATIONALE_OVERALL_MIN_CHARS}-"
                f"{_RATIONALE_OVERALL_MAX_CHARS} chars; got {len(s)}"
            )
        return s


# =============================================================================
# Pydantic — persisted side
# =============================================================================
class MGSRDecision(BaseModel):
    """The final decision: halt/continue + actions + reasons. Combines
    the LLM's emitted intent with the node's halt-reason derivation."""
    halt:              bool
    halt_reason:       HaltReason
    confidence:        float
    actions:           list[ReplanAction]
    rationale_overall: str


class MGSRReplan(BaseModel):
    """Full replan blob persisted to MinIO."""
    schema_version:           str = MGSR_SCHEMA_VERSION
    prompt_version:           str = MGSR_PROMPT_VERSION
    chapter_id:               str
    chapter_title:            str
    framework_slug:           str
    iteration:                int = 0          # v1 always 0; v2 loop bumps
    decision:                 MGSRDecision
    deployment:               Optional[str] = None
    wall_ms:                  Optional[int] = None
    # Triggering input snapshot (for downstream debugging + v2 plateau detection):
    checklist_pass_rate:      float
    checklist_chapter_passed: bool
    n_failed_criteria:        int
    failed_criteria_names:    list[str]


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
