"""mgsr_replan — Memory-Guided Structure Replanner library.

Pure module: Pydantic schemas + prompt templates + deterministic
validators + halt-decision helper. No I/O, no LLM calls — that lives
in `synth/mgsr/node.py`.

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
from .constants import (
    MGSR_PROMPT_VERSION,
    MGSR_SCHEMA_VERSION,
    _CONFIDENCE_HIGH_THRESHOLD,
    _CONFIDENCE_PLATEAU_THRESHOLD,
    _DESCRIPTION_MAX_CHARS,
    _DESCRIPTION_MIN_CHARS,
    _HEADING_MAX_WORDS,
    _HEADING_MIN_WORDS,
    _MAX_ACTIONS_PER_REPLAN,
    _MAX_TARGETS_PER_ACTION,
    _MIN_TARGETS,
    _RATIONALE_MAX_CHARS,
    _RATIONALE_MIN_CHARS,
    _RATIONALE_OVERALL_MAX_CHARS,
    _RATIONALE_OVERALL_MIN_CHARS,
    _SECTION_ID_RE,
)
from .types import (
    HaltReason,
    MGSRDecision,
    MGSRReplan,
    ReplanAction,
    ReplanActionType,
    _LLMReplanPayload,
)
from .service import (
    build_replan_prompt,
    build_repair_prompt,
    build_trivial_pass_decision,
    derive_halt_reason,
    is_trivial_pass,
    validate_actions_against_outline,
    _format_failed_feedback,
    _format_outline_compact,
)

__all__ = [
    # constants
    "MGSR_PROMPT_VERSION",
    "MGSR_SCHEMA_VERSION",
    "_CONFIDENCE_HIGH_THRESHOLD",
    "_CONFIDENCE_PLATEAU_THRESHOLD",
    "_DESCRIPTION_MAX_CHARS",
    "_DESCRIPTION_MIN_CHARS",
    "_HEADING_MAX_WORDS",
    "_HEADING_MIN_WORDS",
    "_MAX_ACTIONS_PER_REPLAN",
    "_MAX_TARGETS_PER_ACTION",
    "_MIN_TARGETS",
    "_RATIONALE_MAX_CHARS",
    "_RATIONALE_MIN_CHARS",
    "_RATIONALE_OVERALL_MAX_CHARS",
    "_RATIONALE_OVERALL_MIN_CHARS",
    "_SECTION_ID_RE",
    # types
    "HaltReason",
    "MGSRDecision",
    "MGSRReplan",
    "ReplanAction",
    "ReplanActionType",
    "_LLMReplanPayload",
    # service
    "build_replan_prompt",
    "build_repair_prompt",
    "build_trivial_pass_decision",
    "derive_halt_reason",
    "is_trivial_pass",
    "validate_actions_against_outline",
    "_format_failed_feedback",
    "_format_outline_compact",
]
