"""mgsr — Pydantic schemas (LLM output + persisted decision/replan blob)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .params import (
    DESCRIPTION_MAX_CHARS,
    DESCRIPTION_MIN_CHARS,
    HEADING_MAX_WORDS,
    HEADING_MIN_WORDS,
    MAX_ACTIONS_PER_REPLAN,
    MAX_TARGETS_PER_ACTION,
    MIN_TARGETS,
    RATIONALE_MAX_CHARS,
    RATIONALE_MIN_CHARS,
    RATIONALE_OVERALL_MAX_CHARS,
    RATIONALE_OVERALL_MIN_CHARS,
)
from .patterns import SECTION_ID_RE
from .versions import MGSR_PROMPT_VERSION, MGSR_SCHEMA_VERSION


ReplanActionType = Literal["merge", "delete", "rename", "reorder", "add"]
HaltReason = Literal[
    "chapter_passed",          # trivial — pass_rate ≥ 0.80, no LLM call
    "no_actions_needed",       # LLM emitted zero actions, halt=true
    "confidence_high",         # LLM confidence > CONFIDENCE_HIGH_THRESHOLD
    "confidence_plateau",      # iteration history shows plateau (v2 only)
    "budget_exhausted",        # iteration count hit cap (v2 only)
    "v1_no_loop",              # actions emitted but v1 doesn't loop yet
]


class ReplanAction(BaseModel):
    """One structured replan action over the outline DAG.

    Action vocabulary verbatim from SurveyGen-I §3.2.3:
      - merge:   combine targets into one section (first target = new id)
      - delete:  remove target section(s); cleanup downstream prereqs
      - rename:  change heading/description of a section
      - reorder: move a section in reading order (insert_before/after)
      - add:     create a new section (insert position required)
    """
    action:    ReplanActionType
    targets:   list[str] = Field(
        default_factory = list,
        description = (
            "section_ids the action operates on. `add` may have empty "
            "targets; others must reference EXISTING section_ids from "
            "the current outline."
        ),
    )
    rationale: str = Field(
        description = (
            f"{RATIONALE_MIN_CHARS}-{RATIONALE_MAX_CHARS} chars. Why "
            f"THIS specific action — which failed criterion does it "
            f"address."
        ),
    )
    new_heading:        Optional[str] = Field(
        default = None,
        description = (
            "REQUIRED for merge/rename/add. 2-8 words, like outline_sdp's "
            "heading rules."
        ),
    )
    new_description:    Optional[str] = Field(
        default = None,
        description = (
            "REQUIRED for merge/rename/add. 20-400 chars, like "
            "outline_sdp's description rules."
        ),
    )
    new_prerequisites:  Optional[list[str]] = Field(
        default = None,
        description = (
            "OPTIONAL for add. List of section_ids the new section "
            "depends on."
        ),
    )
    insert_after:       Optional[str] = Field(
        default = None,
        description = (
            "REQUIRED for reorder/add (exactly one of insert_after / "
            "insert_before). section_id that this section should appear "
            "AFTER in reading order."
        ),
    )
    insert_before:      Optional[str] = Field(
        default = None,
        description = (
            "REQUIRED for reorder/add (exactly one of insert_after / "
            "insert_before). section_id that this section should appear "
            "BEFORE."
        ),
    )

    @field_validator("targets")
    @classmethod
    def _validate_targets_format(cls, v: list[str]) -> list[str]:
        for t in v:
            if not SECTION_ID_RE.match(t):
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
        if not (RATIONALE_MIN_CHARS <= len(s) <= RATIONALE_MAX_CHARS):
            raise ValueError(
                f"rationale must be {RATIONALE_MIN_CHARS}-"
                f"{RATIONALE_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("new_heading")
    @classmethod
    def _validate_heading(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip()
        words = s.split()
        if not (HEADING_MIN_WORDS <= len(words) <= HEADING_MAX_WORDS):
            raise ValueError(
                f"new_heading must be {HEADING_MIN_WORDS}-"
                f"{HEADING_MAX_WORDS} words; got {len(words)} ({s!r})"
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
        if not (DESCRIPTION_MIN_CHARS <= len(s) <= DESCRIPTION_MAX_CHARS):
            raise ValueError(
                f"new_description must be {DESCRIPTION_MIN_CHARS}-"
                f"{DESCRIPTION_MAX_CHARS} chars; got {len(s)}"
            )
        return s

    @field_validator("new_prerequisites")
    @classmethod
    def _validate_prereqs(
        cls, v: Optional[list[str]],
    ) -> Optional[list[str]]:
        if v is None:
            return None
        for p in v:
            if not SECTION_ID_RE.match(p):
                raise ValueError(
                    f"new_prerequisites entry {p!r} must match section_id format"
                )
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate prerequisites: {v}")
        return v

    @model_validator(mode = "after")
    def _validate_action_fields(self) -> "ReplanAction":
        """Enforce action-specific required-field combinations."""
        min_targets = MIN_TARGETS[self.action]
        if len(self.targets) < min_targets:
            raise ValueError(
                f"action {self.action!r} requires ≥{min_targets} "
                f"targets; got {len(self.targets)}"
            )
        if len(self.targets) > MAX_TARGETS_PER_ACTION:
            raise ValueError(
                f"action {self.action!r} has {len(self.targets)} "
                f"targets; max {MAX_TARGETS_PER_ACTION}"
            )

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
                    "reorder must specify EXACTLY ONE of insert_after / "
                    "insert_before"
                )
        elif self.action == "add":
            if self.targets:
                raise ValueError(
                    "add must have empty `targets` (the new section "
                    "gets an auto-assigned id during apply_actions)"
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
                    "add must specify EXACTLY ONE of insert_after / "
                    "insert_before"
                )
        # delete has no extra required fields beyond targets ≥ 1

        return self


class LLMReplanPayload(BaseModel):
    """What the LLM emits when it has to replan. The node wraps this
    into the final MGSRDecision after computing halt-reason."""
    actions: list[ReplanAction] = Field(
        default_factory = list,
        description = (
            f"0-{MAX_ACTIONS_PER_REPLAN} replan actions. Surgical > "
            f"broad — emit only the minimum set that addresses the "
            f"failed criteria. Empty list with halt=true is the correct "
            f"response when the chapter is structurally fine and the "
            f"failed criteria are aesthetic preferences."
        ),
    )
    halt:              bool = Field(
        description = (
            "Whether to STOP the replan loop. true = chapter is good "
            "enough or no actions would meaningfully help. false = the "
            "emitted actions would improve quality if applied."
        ),
    )
    confidence:        float = Field(
        ge = 0.0, le = 1.0,
        description = (
            "Self-reported confidence (0-1) that no further actions are "
            "needed beyond the ones listed. Used by mgsr_replan's "
            "CoRefine-style halt logic (> 0.85 → halt regardless of "
            "halt flag value)."
        ),
    )
    rationale_overall: str = Field(
        description = (
            f"{RATIONALE_OVERALL_MIN_CHARS}-{RATIONALE_OVERALL_MAX_CHARS} "
            f"chars. 1-paragraph summary of the replan strategy."
        ),
    )

    @field_validator("actions")
    @classmethod
    def _validate_action_count(
        cls, v: list[ReplanAction],
    ) -> list[ReplanAction]:
        if len(v) > MAX_ACTIONS_PER_REPLAN:
            raise ValueError(
                f"actions count {len(v)} exceeds max "
                f"{MAX_ACTIONS_PER_REPLAN}"
            )
        return v

    @field_validator("rationale_overall")
    @classmethod
    def _validate_overall(cls, v: str) -> str:
        s = " ".join(v.strip().split())
        if not (
            RATIONALE_OVERALL_MIN_CHARS <= len(s) <= RATIONALE_OVERALL_MAX_CHARS
        ):
            raise ValueError(
                f"rationale_overall must be {RATIONALE_OVERALL_MIN_CHARS}"
                f"-{RATIONALE_OVERALL_MAX_CHARS} chars; got {len(s)}"
            )
        return s


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
    # Triggering input snapshot (for downstream debugging + v2 plateau detection).
    checklist_pass_rate:      float
    checklist_chapter_passed: bool
    n_failed_criteria:        int
    failed_criteria_names:    list[str]


REPLAN_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name":   "mgsr_replan",
        "schema": LLMReplanPayload.model_json_schema(),
        "strict": False,
    },
}
